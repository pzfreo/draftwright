//! Contacts on the finished drawing that the placement collision check
//! ([`super::placement`]) passes as clear but a reader sees as touching or as one line (#226):
//! text closer than [`TEXT_CLEAR`] to another item's text or line-work, an annotation on a
//! section's arrowhead or letter, an extension line just beside a centre line, a leader
//! running along a centre line, a leader ending on an edge its view shows hidden, and a long
//! leader running across its own view. Judged once on the selected drawing, after its final
//! route repair, so they report what is printed without changing paper choice or placement.
use super::placement::{boxes_overlap, crosses, ink_through};
use super::{Placed, PlannedDimension};
use crate::centrelines::LineWork;
use crate::model::{Analysis, Plan, PlannedView, Problem};
use crate::output::{Curve, P};

/// The least clear space a label keeps from another item's label or line-work, and from a
/// section's printed marks, page mm: a glyph box closer than this reads as touching the other
/// ink. It is the gap an axis end keeps from a label ([`crate::centrelines`]).
pub(crate) const TEXT_CLEAR: f64 = 0.5;
/// Two parallel lines closer than this print as one, page mm: an extension line this near a
/// centre line lies on it.
const SAME_LINE: f64 = 0.01;
/// A leader within this angle of a centre line, radians (6°), and within [`ALONG_GAP`] of it
/// over more than [`ALONG_LENGTH`], reads as that centre line. A radial leader leaving a hole's
/// rim shares the axis's overrun past the rim by construction (at most 3 mm,
/// [`crate::centrelines::overrun`]), so the length must exceed it with a margin. hirth-joint's
/// ⌀8 THRU leader, 5.2° off its axis, reads as it; gt2-pulley's two leaders fanning onto a rim
/// either side of an axis, about 8° off it, read as their own lines.
const ALONG_ANGLE: f64 = 6.0 * std::f64::consts::PI / 180.0;
pub(super) const ALONG_GAP: f64 = 2.0;
const ALONG_LENGTH: f64 = 5.0;
/// A callout leader at least this long, page mm, that crosses at least [`EXTRA_LINES`] more of
/// its view's lines (visible or hidden) than the clearest straight way out of the view from its
/// tip, among [`RAYS`] directions, runs across the view to a label it could have had nearby.
/// cgb245's two 2× R3 leaders, 25 and 27 mm long, cross seven and eight more lines of its plan
/// on their way across it; a fillet leader in a dense view crossing a few edges within 11 mm
/// (ftc-08's 8× R1.524) stays local, as does gt2-pulley's ⌀5 THRU, whose every way out of the
/// flange crosses the same eleven lines.
const LONG_LEADER: f64 = 15.0;
const EXTRA_LINES: usize = 4;
const RAYS: usize = 72;
/// Crossings are counted from this far along a leader from its tip, page mm, so the edge the
/// tip lands on is not one of them.
const TIP_GAP: f64 = 0.3;

/// The finding codes this pass owns. `label_section_furniture_overlap` shares lint's code
/// (`crate::lint::structural`), which reports a label overlapping a mark; this pass reports
/// line-work through a mark under it, and ink within [`TEXT_CLEAR`] of one as `label_crowded`.
const CODES: [&str; 6] = [
    "label_crowded",
    "label_section_furniture_overlap",
    "extension_off_centreline",
    "leader_along_centreline",
    "leader_to_hidden_edge",
    "leader_across_view",
];

/// A label box *b* grown by [`TEXT_CLEAR`] on every side; an empty box stays empty.
pub(super) fn clear_box(b: [f64; 4]) -> [f64; 4] {
    if b[2] <= b[0] || b[3] <= b[1] {
        return b;
    }
    [
        b[0] - TEXT_CLEAR,
        b[1] - TEXT_CLEAR,
        b[2] + TEXT_CLEAR,
        b[3] + TEXT_CLEAR,
    ]
}

fn named(d: &PlannedDimension) -> String {
    crate::wording::item(&d.id, &d.kind, &d.label)
}

/// The structural lint's finite-chain warning, measured without a render or
/// changing the plan's published problem inventory.
pub(crate) fn fixed_stroke_contacts(plan: &Plan) -> Vec<Problem> {
    let mut out = Vec::new();
    for d in plan.dimensions.iter().filter(|d| d.kind == "callout") {
        let Some(p) = &d.placed else {
            continue;
        };
        let Some(owner) = plan.views.iter().find(|v| v.id == d.view) else {
            continue;
        };
        for v in plan.views.iter().filter(|v| v.sheet == owner.sheet) {
            for w in v.line_work.iter().filter(|w| w.kind != "cutting_plane") {
                if crate::lint::structural::leader_follows_stroke(
                    &p.lines,
                    &crate::centrelines::segments(w),
                ) {
                    out.push(Problem {
                        id: format!("p:leader_centerline_overlap:{}|{}", d.id, w.id),
                        code: "leader_centerline_overlap".into(),
                        severity: "warning".into(),
                        subjects: vec![d.id.clone(), w.id.clone()],
                        ..Default::default()
                    });
                }
            }
        }
    }
    out
}

/// A callout's text keeps this from another dimension's line, page mm: the gap the callout
/// router leaves between a shoulder and its text, and its padding around a settled label.
const NOTE_CLEAR: f64 = 1.0;

/// A linear dimension's own line or arrowhead within [`NOTE_CLEAR`] of a callout's text in
/// the same view: beside its first or last glyph, or in the gap between the text and its
/// shoulder, where it reads as part of the note. It crosses neither the label box nor the
/// leader and may stay outside [`TEXT_CLEAR`], so no contact above reports it; the lane placer
/// pads only extension lines around a settled callout, and the router scores the clearance
/// without requiring it. Measured for the sheet search as a code and its two items, never
/// published: a larger scale must not add one.
pub(crate) fn note_line_contacts(plan: &Plan) -> Vec<(String, Vec<String>)> {
    let mut out = Vec::new();
    for c in plan.dimensions.iter().filter(|d| d.kind == "callout") {
        let Some(t) = c.placed.as_ref().map(|p| p.label_box) else {
            continue;
        };
        if t[2] <= t[0] || t[3] <= t[1] {
            continue;
        }
        let pad = [
            t[0] - NOTE_CLEAR,
            t[1] - NOTE_CLEAR,
            t[2] + NOTE_CLEAR,
            t[3] + NOTE_CLEAR,
        ];
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.kind == "linear" && d.view == c.view)
        {
            let Some(p) = &d.placed else {
                continue;
            };
            // Every dimension-line segment: a line split around its label is two or more.
            if p.lines
                .iter()
                .skip(2)
                .chain(&p.arrows)
                .any(|l| crosses(*l, pad))
            {
                let mut subjects = vec![c.id.clone(), d.id.clone()];
                subjects.sort();
                out.push(("dimension_line_at_note".into(), subjects));
            }
        }
    }
    out
}

/// A candidate's new line/section-arrow contact can be cleared by extending
/// the cutting-plane overrun. Its cut height, view placements and dimension ink
/// stay fixed; accept only a strict contact improvement with no contact regression.
pub(crate) fn clear_new_section_contacts(plan: &mut Plan, reference: &Plan) {
    use std::collections::{BTreeMap, BTreeSet};
    let keys = |p: &Problem| {
        let mut subjects = p.subjects.clone();
        subjects.sort();
        (p.code.clone(), subjects)
    };
    let old: BTreeSet<_> = reference.problems.iter().map(keys).collect();
    let new = |p: &Problem| p.code == "label_section_furniture_overlap" && !old.contains(&keys(p));
    if !plan.problems.iter().any(new) {
        return;
    }
    let counts = |plan: &Plan| {
        let mut out = BTreeMap::<String, usize>::new();
        for p in contacts(plan) {
            *out.entry(p.code).or_default() += 1;
        }
        out
    };
    for vi in 0..plan.views.len() {
        for wi in 0..plan.views[vi].line_work.len() {
            if plan.views[vi].line_work[wi].kind != "cutting_plane" {
                continue;
            }
            for end in 0..2 {
                let before = counts(plan);
                if before
                    .get("label_section_furniture_overlap")
                    .copied()
                    .unwrap_or(0)
                    == 0
                {
                    return;
                }
                let original = plan.views[vi].line_work[wi].clone();
                let page = std::iter::once(&plan.sheet)
                    .chain(&plan.sheets)
                    .find(|s| s.number == plan.views[vi].sheet)
                    .expect("view sheet")
                    .page;
                for extra in [2., 4., 6., 8., 10., 12.] {
                    let mut next = original.clone();
                    let Some(Curve::Line(a, b)) = next.curves.first_mut() else {
                        break;
                    };
                    if end == 0 {
                        a[0] -= extra;
                    } else {
                        b[0] += extra;
                    }
                    let marks =
                        crate::centrelines::section_marks(&plan.views, &plan.views[vi], &next);
                    if marks.iter().any(|(_, _, b)| {
                        b[0] < crate::layout::margin()
                            || b[1] < crate::layout::margin()
                            || b[2] > page[0] - crate::layout::margin()
                            || b[3] > page[1] - crate::layout::margin()
                    }) {
                        continue;
                    }
                    plan.views[vi].line_work[wi] = next;
                    let after = counts(plan);
                    if after
                        .get("label_section_furniture_overlap")
                        .copied()
                        .unwrap_or(0)
                        < before
                            .get("label_section_furniture_overlap")
                            .copied()
                            .unwrap_or(0)
                        && after
                            .iter()
                            .all(|(code, n)| *n <= before.get(code).copied().unwrap_or(0))
                    {
                        break;
                    }
                    plan.views[vi].line_work[wi] = original.clone();
                }
            }
        }
    }
}

/// Replace this pass's findings on *plan* with those of its current ink.
pub(crate) fn report(analysis: &Analysis, plan: &mut Plan) {
    plan.problems.retain(|p| !CODES.contains(&p.code.as_str()));
    let mut found = contacts(plan);
    found.extend(leader_ends(analysis, plan));
    // A frame with no visible rim or place on its size mark leads to its own hidden circle
    // as a last resort ([`super::hole_marks`]).
    for g in plan
        .gdt
        .iter()
        .filter(|g| super::hole_marks::led_to_hidden(g))
        // A frame stacked on the shelf of another shares its leader, reported once.
        .filter(|g| g.placed.as_ref().is_some_and(|p| !p.lines.is_empty()))
    {
        let (surface, reason) = if g.basis.contains(super::hole_marks::CYLINDER_HIDDEN) {
            (
                "its cylinder's rim",
                "no view shows its rim visible and its size mark has no place for it",
            )
        } else {
            (
                "a hole",
                "no view shows its holes visible and their mark has no place for it",
            )
        };
        found.push(problem(
            "leader_to_hidden_edge",
            &g.id,
            vec![g.id.clone(), g.view.clone()],
            format!(
                "The leader of {} ends on {surface} {} shows hidden, so it points at dashed line-work: {reason}.",
                crate::wording::item(&g.id, &g.kind, &g.cells.join(" ")),
                crate::wording::view(&g.view)
            ),
        ));
    }
    plan.problems.extend(found);
    plan.status = crate::completeness::status(&plan.problems);
}

/// A finding identified by *key*. Ink closer than [`TEXT_CLEAR`] but clear is a notice: the
/// text stays legible, unlike an overlap or a crossing; the others mislead the reader and warn.
fn problem(code: &str, key: &str, subjects: Vec<String>, message: String) -> Problem {
    Problem {
        id: format!("p:{code}:{key}"),
        code: code.into(),
        severity: if code == "label_crowded" {
            "info"
        } else {
            "warning"
        }
        .into(),
        message,
        subjects,
        ..Default::default()
    }
}

/// The straight strokes of a view's centre lines (axes and centre marks).
fn centre_lines(v: &PlannedView) -> impl Iterator<Item = (&LineWork, [P; 2])> {
    v.line_work
        .iter()
        .filter(|w| matches!(w.kind.as_str(), "axis" | "cross"))
        .flat_map(|w| {
            w.curves.iter().filter_map(move |c| match c {
                Curve::Line(a, b) => Some((w, [*a, *b])),
                _ => None,
            })
        })
}

/// For a stroke *s* and a centre-line stroke *c*: the sine of the angle between them, the
/// signed perpendicular offset of each end of *s* from the line through *c*, and the stretch of
/// *s* alongside *c*'s extent, as an interval of its parameter (empty when it is not). None for
/// a degenerate stroke.
fn beside(s: [P; 2], c: [P; 2]) -> Option<(f64, [f64; 2], [f64; 2])> {
    let u = [c[1][0] - c[0][0], c[1][1] - c[0][1]];
    let lu = u[0].hypot(u[1]);
    let v = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
    let lv = v[0].hypot(v[1]);
    if lu < 1e-9 || lv < 1e-9 {
        return None;
    }
    let sin = (u[0] * v[1] - u[1] * v[0]).abs() / (lu * lv);
    let off = |p: P| ((p[0] - c[0][0]) * u[1] - (p[1] - c[0][1]) * u[0]) / lu;
    let t = |p: P| ((p[0] - c[0][0]) * u[0] + (p[1] - c[0][1]) * u[1]) / lu;
    Some((
        sin,
        [off(s[0]), off(s[1])],
        within(t(s[0]), t(s[1]), 0.0, lu),
    ))
}

/// The parameters in [0, 1] where the linear value from *v0* to *v1* lies in [*lo*, *hi*].
fn within(v0: f64, v1: f64, lo: f64, hi: f64) -> [f64; 2] {
    let slope = v1 - v0;
    if slope.abs() < 1e-12 {
        return if (lo..=hi).contains(&v0) {
            [0.0, 1.0]
        } else {
            [0.0, 0.0]
        };
    }
    let (a, b) = ((lo - v0) / slope, (hi - v0) / slope);
    [a.min(b).max(0.0), a.max(b).min(1.0)]
}

/// Callout leaders against their view's physical edges: a tip on an edge a nearer face hides,
/// and a long shaft crossing [`EXTRA_LINES`] more of the view's lines than the clearest way out
/// of the view from its tip would. A view without the router's edge proof (a section, the
/// isometric, a view with no planar face square to it) is not judged.
fn leader_ends(analysis: &Analysis, plan: &Plan) -> Vec<Problem> {
    let mut out = Vec::new();
    let proofs = super::interior::callout_proofs(analysis, &plan.views);
    for v in &plan.views {
        let Some(proof) = proofs.get(&v.id) else {
            continue;
        };
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.view == v.id && d.kind == "callout")
        {
            let Some(&shaft) = d.placed.as_ref().and_then(|p| p.lines.first()) else {
                continue;
            };
            let tip = shaft[0];
            if proof.hidden_tip(v, tip) {
                out.push(problem(
                    "leader_to_hidden_edge",
                    &d.id,
                    vec![d.id.clone(), v.id.clone()],
                    format!(
                        "The leader of {} ends on an edge {} shows hidden, so it points at dashed line-work.",
                        named(d),
                        crate::wording::view(&v.id)
                    ),
                ));
            }
            let length = (shaft[1][0] - tip[0]).hypot(shaft[1][1] - tip[1]);
            if length < LONG_LEADER {
                continue;
            }
            // Crossings are counted from just off the tip, clear of the edge it lands on.
            let from = |u: P| [tip[0] + TIP_GAP * u[0], tip[1] + TIP_GAP * u[1]];
            let u = [
                (shaft[1][0] - tip[0]) / length,
                (shaft[1][1] - tip[1]) / length,
            ];
            let crossed = proof.lines_crossed(v, [from(u), shaft[1]]);
            if crossed < EXTRA_LINES {
                continue;
            }
            let a = v.area;
            let reach = [[a[0], a[1]], [a[2], a[1]], [a[0], a[3]], [a[2], a[3]]]
                .iter()
                .map(|c| (c[0] - tip[0]).hypot(c[1] - tip[1]))
                .fold(0.0, f64::max)
                + 1.0;
            let least = (0..RAYS)
                .map(|k| {
                    let t = k as f64 * std::f64::consts::TAU / RAYS as f64;
                    let u = [t.cos(), t.sin()];
                    proof
                        .lines_crossed(v, [from(u), [tip[0] + reach * u[0], tip[1] + reach * u[1]]])
                })
                .min()
                .unwrap_or(0);
            if crossed >= least + EXTRA_LINES {
                out.push(problem(
                    "leader_across_view",
                    &d.id,
                    vec![d.id.clone(), v.id.clone()],
                    format!(
                        "The leader of {} runs {length:.1} mm across {}, crossing {crossed} of its lines where a leader leaving the other way would cross {least}.",
                        named(d),
                        crate::wording::view(&v.id)
                    ),
                ));
            }
        }
    }
    out
}

// Conservative broad phase for the exact ink predicate below. Bounds include
// filled arrowheads; uncertain/nonfinite ink always reaches the narrow phase.
fn ink_bounds(p: &Placed) -> [f64; 4] {
    let mut b = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for q in super::placement::ink_points(p) {
        if !q.iter().all(|n| n.is_finite()) {
            return [
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
                f64::INFINITY,
                f64::INFINITY,
            ];
        }
        b = [
            b[0].min(q[0]),
            b[1].min(q[1]),
            b[2].max(q[0]),
            b[3].max(q[1]),
        ];
    }
    b
}
fn may_reach(a: [f64; 4], b: [f64; 4]) -> bool {
    !a.iter().chain(&b).all(|n| n.is_finite())
        || (0..2).all(|k| a[k] <= b[k + 2] + 1e-6 && a[k + 2] >= b[k] - 1e-6)
}

/// Intentionally stacked control frames share a box edge, not crowded text. Require
/// the attachment and feature identity as well as the touching geometry: two unrelated
/// labels, or two separately led frames, still need the ordinary clearance.
fn touching_frame_rows(plan: &Plan, a: &str, b: &str) -> bool {
    let Some(a) = plan.gdt.iter().find(|g| g.id == a) else {
        return false;
    };
    let Some(b) = plan.gdt.iter().find(|g| g.id == b) else {
        return false;
    };
    if a.kind != "control_frame"
        || b.kind != "control_frame"
        || a.view != b.view
        || a.faces.is_empty()
        || a.faces != b.faces
        || !super::hole_marks::attached(a)
        || !super::hole_marks::attached(b)
    {
        return false;
    }
    let (Some(a), Some(b)) = (&a.placed, &b.placed) else {
        return false;
    };
    let (u, v) = (a.label_box, b.label_box);
    (a.lines.is_empty() || b.lines.is_empty())
        && u[0].max(v[0]) < u[2].min(v[2])
        && ((u[1] - v[3]).abs() < 1e-9 || (v[1] - u[3]).abs() < 1e-9)
}

pub(super) fn contacts(plan: &Plan) -> Vec<Problem> {
    let mut all = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(super::gdt::proxy));
    let mut out = Vec::new();
    for sheet in std::iter::once(&plan.sheet).chain(&plan.sheets) {
        let placed: Vec<(&PlannedDimension, &Placed)> = all
            .iter()
            .filter(|d| {
                plan.views
                    .iter()
                    .find(|v| v.id == d.view)
                    .map_or(sheet.number == 1, |v| v.sheet == sheet.number)
            })
            .filter_map(|d| d.placed.as_ref().map(|p| (d, p)))
            .collect();
        let bounds: Vec<_> = placed.iter().map(|(_, p)| ink_bounds(p)).collect();
        // Text near text, and line-work near text. Overlaps and lines through a label are the
        // placement check's; only the contacts it passes as clear are reported here.
        for (i, (a, pa)) in placed.iter().enumerate() {
            for (b, pb) in &placed[i + 1..] {
                if !boxes_overlap(pa.label_box, pb.label_box)
                    && boxes_overlap(clear_box(pa.label_box), pb.label_box)
                    && !touching_frame_rows(plan, &a.id, &b.id)
                {
                    out.push(problem(
                        "label_crowded",
                        &format!("{}|{}", a.id, b.id),
                        vec![a.id.clone(), b.id.clone()],
                        format!(
                            "The labels of {} and {} are less than {TEXT_CLEAR} mm apart, so they read as one.",
                            named(a),
                            named(b)
                        ),
                    ));
                }
            }
        }
        // One finding per label, naming every item whose line-work touches it: a shared
        // baseline is drawn by each dimension standing on it.
        for (b, pb) in &placed {
            let near: Vec<&PlannedDimension> = placed
                .iter()
                .enumerate()
                .filter(|(i, (a, pa))| {
                    a.id != b.id
                        && may_reach(bounds[*i], clear_box(pb.label_box))
                        && !boxes_overlap(clear_box(pa.label_box), pb.label_box)
                        && !ink_through(pa, pb.label_box)
                        && ink_through(pa, clear_box(pb.label_box))
                })
                .map(|(_, (a, _))| *a)
                .collect();
            let Some(first) = near.first() else {
                continue;
            };
            let mut subjects = vec![first.id.clone(), b.id.clone()];
            subjects.extend(near[1..].iter().map(|a| a.id.clone()));
            let more = match near.len() - 1 {
                0 => String::new(),
                1 => format!(" and of {}", named(near[1])),
                n => format!(" and of {n} other items"),
            };
            out.push(problem(
                "label_crowded",
                &subjects.join("|"),
                subjects.clone(),
                format!(
                    "Line-work of {}{more} passes less than {TEXT_CLEAR} mm from the label of {}, so it touches the text.",
                    named(first),
                    named(b)
                ),
            ));
        }
        for v in plan.views.iter().filter(|v| v.sheet == sheet.number) {
            // A section's arrowheads and letters: printed ink that placement steers around.
            for w in &v.line_work {
                let marks = crate::centrelines::section_marks(&plan.views, v, w);
                if marks.is_empty() {
                    continue;
                }
                let section = w.id.rsplit(':').next().unwrap_or_default();
                // The section view the mark names, else the view it is marked in.
                let named_view = plan
                    .views
                    .iter()
                    .find(|s| s.cut.as_ref().is_some_and(|c| c.label == section))
                    .map_or(&v.id, |s| &s.id);
                for (id, _, mark) in marks {
                    let what = if id.contains(":letter:") {
                        "letter"
                    } else {
                        "arrowhead"
                    };
                    for (d, p) in &placed {
                        let key = format!("{}|{id}", d.id);
                        let subjects = vec![d.id.clone(), named_view.clone()];
                        // A label over the mark is lint's own finding.
                        if boxes_overlap(p.label_box, mark) {
                            continue;
                        }
                        let (code, message) = if p.lines.iter().any(|s| crosses(*s, mark)) {
                            (
                                "label_section_furniture_overlap",
                                format!(
                                    "A line of {} runs through the section {section} cutting-plane {what}.",
                                    named(d)
                                ),
                            )
                        } else if boxes_overlap(clear_box(p.label_box), mark)
                            || p.lines.iter().any(|s| crosses(*s, clear_box(mark)))
                        {
                            (
                                "label_crowded",
                                format!(
                                    "{} is less than {TEXT_CLEAR} mm from the section {section} cutting-plane {what}, so it reads as part of the mark.",
                                    crate::wording::capital(&named(d))
                                ),
                            )
                        } else {
                            continue;
                        };
                        out.push(problem(code, &key, subjects, message));
                    }
                }
            }
            // Extension lines and leaders against this view's centre lines.
            // Each item is reported once per code, at the first centre line it meets.
            for (d, p) in placed.iter().filter(|(d, _)| d.view == v.id) {
                let mut seen = std::collections::BTreeSet::new();
                for (w, c) in centre_lines(v) {
                    let key = format!("{}|{}", d.id, w.id);
                    // The centre line's feature, where it is drawn for one.
                    let mut subjects = vec![d.id.clone()];
                    subjects.extend(
                        w.id.strip_prefix(&format!("ink:{}:", v.id))
                            .filter(|f| f.starts_with("f:"))
                            .map(String::from),
                    );
                    if d.kind == "linear" && p.lines.len() >= 3 {
                        let near = p.lines[..2].iter().filter_map(|e| beside(*e, c)).find_map(
                            |(sin, off, along)| {
                                let gap = (0.5 * (off[0] + off[1])).abs();
                                (sin < 1e-6
                                    && along[1] > along[0]
                                    && gap > SAME_LINE
                                    && gap < TEXT_CLEAR)
                                    .then_some(gap)
                            },
                        );
                        if let Some(gap) = near
                            && seen.insert("extension_off_centreline")
                        {
                            out.push(problem(
                                "extension_off_centreline",
                                &key,
                                subjects.clone(),
                                format!(
                                    "An extension line of {} runs {gap:.2} mm beside a centre line, so it reads as locating that centre, which it does not.",
                                    named(d)
                                ),
                            ));
                        }
                    }
                    if d.kind == "callout"
                        && let Some(&shaft) = p.lines.first()
                        && let Some((_, off, _)) = beside(shaft, c)
                        // Exactly collinear strokes are lint's `leader_centerline_overlap`.
                        && off.iter().any(|o| o.abs() > 1e-6)
                        && along_centreline(shaft, c)
                        && seen.insert("leader_along_centreline")
                    {
                        out.push(problem(
                            "leader_along_centreline",
                            &key,
                            subjects,
                            format!(
                                "The leader of {} runs along a centre line, so the two read as one line.",
                                named(d)
                            ),
                        ));
                    }
                }
            }
        }
    }
    out
}

/// Whether leader *shaft* reads as centre-line stroke *c*: within [`ALONG_ANGLE`] of it and
/// within [`ALONG_GAP`] of it over more than [`ALONG_LENGTH`]. The leader router weighs a route
/// this reports as running beside the stroke ([`super::leaders`]), so it keeps clear of what
/// this pass reports.
pub(super) fn along_centreline(shaft: [P; 2], c: [P; 2]) -> bool {
    beside(shaft, c).is_some_and(|(sin, off, along)| {
        sin < ALONG_ANGLE.sin() && near_length(shaft, off, along) > ALONG_LENGTH
    })
}

/// The length of *shaft* both within [`ALONG_GAP`] of a centre line, its ends' signed offsets
/// *off* varying linearly along it, and *along* (the parameter interval alongside the centre
/// line's extent).
fn near_length(shaft: [P; 2], off: [f64; 2], along: [f64; 2]) -> f64 {
    let length = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
    let near = within(off[0], off[1], -ALONG_GAP, ALONG_GAP);
    (near[1].min(along[1]) - near[0].max(along[0])).max(0.0) * length
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Spec;

    fn analysis() -> Analysis {
        serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
        }))
        .unwrap()
    }

    fn plan() -> Plan {
        let mut plan = crate::model::plan(&analysis(), &Spec::default()).unwrap();
        plan.gdt.clear();
        plan
    }

    fn item(
        base: &PlannedDimension,
        id: &str,
        kind: &str,
        lines: Vec<[P; 2]>,
        label_box: [f64; 4],
    ) -> PlannedDimension {
        let mut d = base.clone();
        d.id = id.into();
        d.kind = kind.into();
        d.view = "v:front".into();
        d.placed = Some(Placed {
            lines,
            arrows: Vec::new(),
            label_at: [label_box[0], label_box[1]],
            rotation: 0.,
            label_box,
        });
        d
    }

    fn found(plan: &Plan) -> Vec<(String, Vec<String>)> {
        contacts(plan)
            .into_iter()
            .map(|p| (p.code, p.subjects))
            .collect()
    }

    fn front(plan: &mut Plan) -> &mut PlannedView {
        plan.views.iter_mut().find(|v| v.id == "v:front").unwrap()
    }

    #[test]
    fn a_line_touching_text_is_crowded_once_per_label() {
        let mut p = plan();
        let base = p.dimensions[0].clone();
        let label = [50., 50., 54., 52.];
        let peer = item(&base, "d:peer", "linear", vec![], label);
        // 0.3 mm below the text: a contact the placement check passes as clear.
        let a = item(
            &base,
            "d:a",
            "linear",
            vec![[[40., 49.7], [60., 49.7]]],
            [10., 10., 12., 12.],
        );
        let b = item(
            &base,
            "d:b",
            "linear",
            vec![[[40., 49.7], [70., 49.7]]],
            [20., 10., 22., 12.],
        );
        p.dimensions = vec![peer.clone(), a.clone(), b];
        assert_eq!(
            found(&p),
            [(
                "label_crowded".into(),
                vec!["d:a".into(), "d:peer".into(), "d:b".into()]
            )]
        );
        // Clear by more than the clearance: nothing.
        let mut far = a.clone();
        far.placed.as_mut().unwrap().lines = vec![[[40., 49.4], [60., 49.4]]];
        p.dimensions = vec![peer.clone(), far];
        assert!(found(&p).is_empty());
        // Through the text: the placement check's `annotation_ink_overlap`, not repeated here.
        let mut through = a;
        through.placed.as_mut().unwrap().lines = vec![[[40., 51.], [60., 51.]]];
        p.dimensions = vec![peer, through];
        assert!(found(&p).is_empty());
    }

    /// A dimension line split around its label is two or more segments after the extension
    /// lines; a segment other than the first beside a callout's text is a contact too.
    #[test]
    fn every_segment_of_a_split_dimension_line_is_read_beside_a_note() {
        let mut p = plan();
        let base = p.dimensions[0].clone();
        let note = item(&base, "d:note", "callout", vec![], [50., 50., 60., 52.]);
        let far = [[10., 30.], [20., 30.]];
        // Two extension lines, then the dimension line in two parts around its label: the
        // second part ends 0.5 mm from the note's text.
        let split = item(
            &base,
            "d:split",
            "linear",
            vec![far, far, far, [[30., 51.], [49.5, 51.]]],
            [10., 10., 12., 12.],
        );
        p.dimensions = vec![note.clone(), split];
        assert_eq!(
            note_line_contacts(&p),
            [(
                "dimension_line_at_note".into(),
                vec!["d:note".into(), "d:split".into()]
            )]
        );
        // The same line in one part is read as before, and one clear of the text is not.
        let whole = item(
            &base,
            "d:whole",
            "linear",
            vec![far, far, [[30., 51.], [49.5, 51.]]],
            [10., 10., 12., 12.],
        );
        p.dimensions = vec![note.clone(), whole];
        assert_eq!(note_line_contacts(&p).len(), 1);
        let clear = item(
            &base,
            "d:clear",
            "linear",
            vec![far, far, far, [[30., 51.], [48.5, 51.]]],
            [10., 10., 12., 12.],
        );
        p.dimensions = vec![note, clear];
        assert!(note_line_contacts(&p).is_empty());
    }

    #[test]
    fn labels_closer_than_the_clearance_are_crowded() {
        let mut p = plan();
        let base = p.dimensions[0].clone();
        let a = item(&base, "d:a", "linear", vec![], [50., 50., 54., 52.]);
        let near = item(&base, "d:b", "linear", vec![], [54.3, 50., 58., 52.]);
        p.dimensions = vec![a.clone(), near];
        assert_eq!(
            found(&p),
            [("label_crowded".into(), vec!["d:a".into(), "d:b".into()])]
        );
        let clear = item(&base, "d:b", "linear", vec![], [54.6, 50., 58., 52.]);
        p.dimensions = vec![a, clear];
        assert!(found(&p).is_empty());
    }

    #[test]
    fn only_attached_rows_on_the_same_feature_may_share_a_frame_edge() {
        let mut p = plan();
        p.dimensions.clear();
        let frame = |id: &str, bottom: f64| {
            serde_json::from_value::<super::super::gdt::PlannedGdt>(serde_json::json!({
                "id": id, "kind": "control_frame", "view": "v:front", "side": "below",
                "source": id, "faces": [1,2], "cells": ["⌖", "0.1"], "modifiers": [],
                "label": "⌖ 0.1", "default": true, "state": {"kind": "shown"},
                "basis": "beneath the control frame of its hole, which sees the holes end-on",
                "placed": {
                    "tip": [50, bottom + 5.], "lines": [], "arrows": [],
                    "label_box": [50, bottom, 65, bottom + 5.],
                    "strokes": [], "arcs": [], "fills": [], "letters": [], "runs": []
                }
            }))
            .unwrap()
        };
        p.gdt = vec![frame("pmi:upper", 50.), frame("pmi:lower", 45.)];
        assert!(found(&p).is_empty(), "an intentional shared box edge");
        // Rows may be offset to fit their corridor while still sharing an edge.
        p.gdt[0].placed.as_mut().unwrap().label_box[0] += 1.;
        assert!(found(&p).is_empty());
        // The same geometry on another feature must still be reported.
        p.gdt[1].faces = vec![3];
        assert_eq!(found(&p)[0].0, "label_crowded");
        p.gdt[1].faces = vec![1, 2];
        p.gdt[1].basis.clear();
        assert_eq!(found(&p)[0].0, "label_crowded");
        // Merely being nearby, rather than sharing the edge, is still crowded.
        p.gdt[1] = frame("pmi:lower", 44.7);
        assert_eq!(found(&p)[0].0, "label_crowded");
        // Independently led frames are not a stack, even on the same feature.
        p.gdt[1] = frame("pmi:lower", 45.);
        for g in &mut p.gdt {
            g.placed.as_mut().unwrap().lines = vec![[[45., 45.], [50., 45.]]];
        }
        assert_eq!(found(&p)[0].0, "label_crowded");
    }

    #[test]
    fn a_label_on_a_section_letter_is_reported() {
        let mut p = plan();
        let base = p.dimensions[0].clone();
        let mut cut = front(&mut p)
            .line_work
            .first()
            .cloned()
            .unwrap_or(LineWork {
                id: String::new(),
                kind: String::new(),
                curves: Vec::new(),
                centre: None,
                radial: false,
                global_axis: false,
                overrun: 0.,
            });
        cut.id = "ink:v:front:cut:A".into();
        cut.kind = "cutting_plane".into();
        cut.curves = vec![Curve::Line([20., 100.], [80., 100.])];
        front(&mut p).line_work.push(cut.clone());
        let view = p.views.iter().find(|v| v.id == "v:front").unwrap();
        let marks = crate::centrelines::section_marks(&p.views, view, &cut);
        let (letter, _, b) = marks
            .iter()
            .find(|m| m.0.ends_with(":letter:1"))
            .unwrap()
            .clone();
        // Touching the letter's box from the right, 0.2 mm clear.
        let touching = item(
            &base,
            "d:a",
            "linear",
            vec![],
            [b[2] + 0.2, b[1], b[2] + 4., b[3]],
        );
        p.dimensions = vec![touching.clone()];
        assert_eq!(
            found(&p),
            [("label_crowded".into(), vec!["d:a".into(), "v:front".into()])]
        );
        // A line through the letter is reported here; a label over it is lint's.
        let through = item(
            &base,
            "d:a",
            "linear",
            vec![[
                [b[0] - 5., 0.5 * (b[1] + b[3])],
                [b[2] + 5., 0.5 * (b[1] + b[3])],
            ]],
            [0., 0., 2., 2.],
        );
        p.dimensions = vec![through];
        assert_eq!(
            found(&p),
            [(
                "label_section_furniture_overlap".into(),
                vec!["d:a".into(), "v:front".into()]
            )]
        );
        // The mark is the finding's identity; its section view the subject, a live item.
        assert!(contacts(&p)[0].id.ends_with(&format!("|{letter}")));
        let over = item(
            &base,
            "d:a",
            "linear",
            vec![],
            [b[0] + 0.5, b[1], b[2] + 4., b[3]],
        );
        p.dimensions = vec![over];
        assert!(found(&p).is_empty());
        // Re-reporting replaces the earlier findings rather than repeating them.
        p.dimensions = vec![touching];
        report(&analysis(), &mut p);
        report(&analysis(), &mut p);
        assert_eq!(
            p.problems
                .iter()
                .filter(|q| q.code == "label_crowded")
                .count(),
            1
        );
    }

    #[test]
    fn extension_lines_beside_a_centre_line_and_leaders_along_one_are_reported() {
        let mut p = plan();
        let base = p.dimensions[0].clone();
        let mut axis = LineWork {
            id: "ink:v:front:f:hole:1".into(),
            kind: "axis".into(),
            curves: vec![Curve::Line([30., 40.], [70., 40.])],
            centre: None,
            radial: false,
            global_axis: false,
            overrun: 0.,
        };
        front(&mut p).line_work.push(axis.clone());
        let linear = |offset: f64| {
            item(
                &base,
                "d:a",
                "linear",
                vec![
                    [[60., 40. + offset], [90., 40. + offset]],
                    [[60., 10.], [90., 10.]],
                    [[88., 10.], [88., 40. + offset]],
                ],
                [89., 20., 91., 24.],
            )
        };
        let code = |p: &Plan| found(p).into_iter().map(|f| f.0).collect::<Vec<_>>();
        p.dimensions = vec![linear(0.1)];
        assert_eq!(code(&p), ["extension_off_centreline"]);
        // On the centre line, it locates that centre; well clear, it reads as its own line.
        p.dimensions = vec![linear(0.0)];
        assert!(code(&p).is_empty());
        p.dimensions = vec![linear(1.0)];
        assert!(code(&p).is_empty());
        let callout = |shaft: [P; 2]| {
            item(
                &base,
                "d:c",
                "callout",
                vec![shaft, [shaft[1], [shaft[1][0] + 3., shaft[1][1]]]],
                [100., 100., 110., 102.],
            )
        };
        // A shallow leader 1 mm off the axis over 10 mm, then a steep one.
        p.dimensions = vec![callout([[40., 41.], [50., 41.5]])];
        assert_eq!(code(&p), ["leader_along_centreline"]);
        // About 5.3° off, 0.7 to 2 mm from the axis over 14 mm: still read as the axis (hirth).
        p.dimensions = vec![callout([[40., 40.7], [54., 42.]])];
        assert_eq!(code(&p), ["leader_along_centreline"]);
        // Two leaders fanning onto a rim beside the axis, about 8° off it, read as their own.
        p.dimensions = vec![callout([[40., 41.], [54., 43.]])];
        assert!(code(&p).is_empty());
        p.dimensions = vec![callout([[40., 41.], [45., 60.]])];
        assert!(code(&p).is_empty());
        // Exactly collinear strokes are lint's leader_centerline_overlap.
        p.dimensions = vec![callout([[40., 40.], [50., 40.]])];
        assert!(code(&p).is_empty());
        axis.kind = "pitch_circle".into();
        front(&mut p).line_work.pop();
        front(&mut p).line_work.push(axis);
        p.dimensions = vec![callout([[40., 41.], [50., 41.5]])];
        assert!(code(&p).is_empty());
    }

    #[test]
    fn the_sheet_basis_states_conflicts_left_by_the_final_route_repair() {
        let mut p = plan();
        p.sheet.size = "A4".into();
        p.sheet.scale = "1:1".into();
        let stated = crate::model::ink_findings(&p);
        p.sheet.basis = format!(
            "candidates: A4 2:1 fill 70.0% (3 hard layout findings, 9 annotation conflicts); \
             A4 1:1 fill 50.0% (valid and complete, {stated} annotation conflicts, columns)"
        );
        let before = p.sheet.basis.clone();
        crate::model::note_final_conflicts(&mut p);
        assert_eq!(p.sheet.basis, before, "an unchanged count adds nothing");
        p.problems.push(Problem {
            code: "annotation_leader_crossing".into(),
            ..Default::default()
        });
        crate::model::note_final_conflicts(&mut p);
        crate::model::note_final_conflicts(&mut p);
        assert!(
            p.sheet.basis.ends_with(&format!(
                "the selected A4 1:1 candidate's annotation conflicts were counted \
                 before its final repairs, which take them from {stated} to {}",
                stated + 1
            )),
            "{}",
            p.sheet.basis
        );
        assert_eq!(p.sheet.basis.matches("final repairs").count(), 1);
    }
}

#[cfg(test)]
mod bounds_tests {
    use super::*;
    #[test]
    fn contact_bounds_never_skip_filled_heads_or_witness_ink() {
        let p = Placed {
            lines: vec![[[-5., 0.], [15., 0.]], [[15., 0.], [15., 8.]]],
            arrows: vec![[[0., 0.], [4., 4.]], [[12., 5.], [10., 2.]]],
            label_at: [20., 20.],
            label_box: [20., 20., 25., 23.],
            rotation: 0.,
        };
        let bound = ink_bounds(&p);
        for x in -7..=27 {
            for y in -7..=25 {
                for size in [0.01, 0.5, 3.] {
                    let b = [
                        f64::from(x),
                        f64::from(y),
                        f64::from(x) + size,
                        f64::from(y) + size,
                    ];
                    assert!(!ink_through(&p, b) || may_reach(bound, b));
                }
            }
        }
        assert!(may_reach(bound, [f64::NAN; 4]));
    }
}
