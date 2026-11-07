//! Where the planned dimensions go on the page, and what then collides: linear dimensions and
//! callouts stacked in lanes beside their view, each collision a problem on the dimension's ID.

use std::collections::BTreeMap;

use haecceity::hlr::View;

use super::holes::SPOTFACE;
use super::{
    ARROW, ARROW_HALF, CAP, GAP, LIFT, OVERSHOOT, P2, P3, Placed, PlannedDimension, Shape, TEXT,
    TOKEN_GAP, Want, first_line, font, key, lane_pitch,
};
use crate::layout::{FRAME, Lanes, margin};
use crate::model::{Analysis, PlannedSheet, PlannedView, Problem};
use crate::output::{STRAY_TEXT, label_gap, linear_ink, shared_dimension_line};
use crate::text::{Font, Segment};

/// A held lane an item may share: its strip, the lane, and the item's extent along it there.
type Shared = (Side, usize, [f64; 2]);

/// Strip lanes: (new recess, rank, sub-rank, item index), per view and strip.
type Strips<'a> = BTreeMap<(&'a str, Side), Vec<(bool, u8, i64, usize)>>;

/// A view as the dimensions see it: world to page, and the part's box on the page.
pub(super) struct Frame {
    view: View,
    broken: Option<crate::broken::Break>,
    at: P2,
    s: f64,
    centre: P2,
    inspection: Option<(usize, f64)>,
    /// The projected envelope's box on the page.
    outline: [f64; 4],
}

impl Frame {
    pub(super) fn memo_geometry(&self) -> Vec<u64> {
        let mut key: Vec<_> = self
            .view
            .x
            .iter()
            .chain(&self.view.y)
            .chain(&self.view.toward)
            .chain(&self.at)
            .chain(std::iter::once(&self.s))
            .chain(&self.centre)
            .chain(&self.outline)
            .map(|n| n.to_bits())
            .collect();
        if let Some(b) = &self.broken {
            key.extend(b.key());
        }
        key
    }

    pub(super) fn new(analysis: &Analysis, v: &PlannedView) -> Frame {
        let mut f = Frame::looking(analysis, v.direction, v.up, v.at, v.scale);
        f.broken = v.broken.clone();
        if v.detail.is_some() || v.broken.is_some() {
            let (size, centre) = crate::detail::crop_extent(analysis, v);
            f.centre = centre;
            f.outline = [
                v.at[0] - size[0] * v.scale / 2.0,
                v.at[1] - size[1] * v.scale / 2.0,
                v.at[0] + size[0] * v.scale / 2.0,
                v.at[1] + size[1] * v.scale / 2.0,
            ];
        }
        f
    }

    /// The view looking along *direction* with *up*, the part's box centred at *at*, at scale *s*.
    pub(super) fn looking(analysis: &Analysis, direction: P3, up: P3, at: P2, s: f64) -> Frame {
        // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
        let view = View::new(direction, up).expect("a planned view");
        let (size, centre) = crate::model::extent(analysis, &view);
        Frame {
            view,
            broken: None,
            at,
            s,
            centre,
            inspection: super::turned::axial_datum(analysis),
            outline: [
                at[0] - 0.5 * s * size[0],
                at[1] - 0.5 * s * size[1],
                at[0] + 0.5 * s * size[0],
                at[1] + 0.5 * s * size[1],
            ],
        }
    }

    /// The projected envelope's box on the page.
    pub(super) fn outline(&self) -> [f64; 4] {
        self.outline
    }

    /// The view's scale, page mm per world mm.
    pub(super) fn scale(&self) -> f64 {
        self.s
    }

    pub(super) fn page(&self, p: P3) -> P2 {
        let q = self.view.map(p);
        let q = self.broken.as_ref().map_or(q, |b| b.map(q));
        [
            self.at[0] + self.s * (q[0] - self.centre[0]),
            self.at[1] + self.s * (q[1] - self.centre[1]),
        ]
    }
}

/// A label laid out in its own frame: the baseline on y = 0 from x = 0.
pub struct LabelInk {
    /// Each text token's glyph outlines.
    pub glyphs: Vec<Vec<Vec<Segment>>>,
    /// The drawn symbols' strokes.
    pub strokes: Vec<[P2; 2]>,
    pub min: P2,
    pub max: P2,
}

/// Lays out *terms* as draftwright's callout recipe does: each term's leading symbol in a cell of
/// one text height (`⌀` as the font's `ø`, the others drawn), each token a gap apart. The
/// spotface symbol (the term [`SPOTFACE`], ASME Y14.5-2018's counterbore symbol with SF inside)
/// is the counterbore's strokes widened to hold the font's own `SF`, in a cell of [`SF_CELL`].
pub fn label_ink(terms: &[String], font: &Font) -> LabelInk {
    if terms.iter().any(|t| crate::callouts::is_break(t)) {
        let mut combined = LabelInk {
            glyphs: Vec::new(),
            strokes: Vec::new(),
            min: [f64::INFINITY; 2],
            max: [f64::NEG_INFINITY; 2],
        };
        for (i, line) in crate::callouts::lines(terms).iter().enumerate() {
            let ink = label_ink(line, font);
            let rise = -(i as f64) * crate::callouts::line_spacing();
            for k in 0..2 {
                let shift = if k == 1 { rise } else { 0.0 };
                combined.min[k] = combined.min[k].min(ink.min[k] + shift);
                combined.max[k] = combined.max[k].max(ink.max[k] + shift);
            }
            let shift = |p: P2| [p[0], p[1] + rise];
            combined
                .strokes
                .extend(ink.strokes.into_iter().map(|[a, b]| [shift(a), shift(b)]));
            combined.glyphs.extend(ink.glyphs.into_iter().map(|g| {
                g.into_iter()
                    .map(|c| c.into_iter().map(|s| moved(&s, &shift)).collect())
                    .collect()
            }));
        }
        return combined;
    }
    let h = TEXT;
    let mut ink = LabelInk {
        glyphs: Vec::new(),
        strokes: Vec::new(),
        min: [f64::INFINITY; 2],
        max: [f64::NEG_INFINITY; 2],
    };
    let grow = |ink: &mut LabelInk, a: P2, b: P2| {
        for p in [a, b] {
            ink.min = [ink.min[0].min(p[0]), ink.min[1].min(p[1])];
            ink.max = [ink.max[0].max(p[0]), ink.max[1].max(p[1])];
        }
    };
    let mut x = 0.0;
    let mut tokens: Vec<(Option<char>, String)> = Vec::new();
    let plain = |t: &str, tokens: &mut Vec<(Option<char>, String)>| {
        let mut chars = t.chars();
        match chars.next() {
            _ if t == SPOTFACE => tokens.push((Some('⌴'), "SF".into())),
            Some(c @ ('⌀' | '↧' | '⌴' | '⌵')) => {
                tokens.push((Some(c), String::new()));
                let rest: String = chars.collect();
                if !rest.is_empty() {
                    tokens.push((None, rest));
                }
            }
            _ => tokens.push((None, t.into())),
        }
    };
    for t in terms {
        // The newline encodes upper/lower values. Reference labels may wrap the whole
        // callout into one term, so split immediately around the numeric pair.
        if let Some((above, below)) = t.split_once('\n') {
            let (prefix, upper) = above.rsplit_once(' ').unwrap_or(("", above));
            let end = below
                .find(|c: char| !c.is_ascii_digit() && !matches!(c, '+' | '-' | '.'))
                .unwrap_or(below.len());
            let (lower, suffix) = below.split_at(end);
            if upper.parse::<f64>().is_ok() && lower.parse::<f64>().is_ok() {
                if !prefix.is_empty() {
                    plain(prefix, &mut tokens);
                }
                tokens.push((Some('≔'), format!("{upper}\n{lower}")));
                if !suffix.trim().is_empty() {
                    plain(suffix.trim(), &mut tokens);
                }
                continue;
            }
        }
        plain(t, &mut tokens);
    }
    for (i, (symbol, text)) in tokens.iter().enumerate() {
        if i > 0 {
            x += TOKEN_GAP;
        }
        let glyphs = |s: &str, at: f64, ink: &mut LabelInk| {
            let laid = font.baseline(s, h);
            if !laid.contours.is_empty() {
                grow(
                    ink,
                    [at + laid.min[0], laid.min[1]],
                    [at + laid.max[0], laid.max[1]],
                );
            }
            let shift = |p: P2| [p[0] + at, p[1]];
            ink.glyphs.push(
                laid.contours
                    .into_iter()
                    .map(|c| c.into_iter().map(|s| moved(&s, &shift)).collect())
                    .collect(),
            );
        };
        match symbol {
            Some('≔') => {
                // Invariant: This branch handles only stacked deviations, constructed with a newline.
                let (upper, lower) = text.split_once('\n').expect("deviation stack");
                let size = 0.7 * h;
                let mut width: f64 = 0.0;
                for (value, rise) in [(upper, 0.55 * CAP), (lower, -0.45 * CAP)] {
                    let laid = font.baseline(value, size);
                    grow(
                        &mut ink,
                        [x + laid.min[0], rise + laid.min[1]],
                        [x + laid.max[0], rise + laid.max[1]],
                    );
                    let shift = |p: P2| [x + p[0], rise + p[1]];
                    ink.glyphs.push(
                        laid.contours
                            .into_iter()
                            .map(|c| c.into_iter().map(|s| moved(&s, &shift)).collect())
                            .collect(),
                    );
                    width = width.max(font.advance(value, size));
                }
                x += width;
            }
            Some('⌀') => {
                let at = x + 0.5 * (h - font.advance("ø", h));
                glyphs("ø", at, &mut ink);
                x += h;
            }
            Some('⌴') if text == "SF" => {
                let (l, r) = (x + 0.15 * h, x + SF_CELL - 0.15 * h);
                let strokes = [
                    [[l, CAP], [l, 0.0]],
                    [[l, 0.0], [r, 0.0]],
                    [[r, 0.0], [r, CAP]],
                ];
                for s in &strokes {
                    grow(&mut ink, s[0], s[1]);
                }
                ink.strokes.extend(strokes);
                let at = x + 0.5 * (SF_CELL - font.advance(text, SF_TEXT));
                let laid = font.baseline(text, SF_TEXT);
                grow(
                    &mut ink,
                    [at + laid.min[0], SF_RAISE + laid.min[1]],
                    [at + laid.max[0], SF_RAISE + laid.max[1]],
                );
                let shift = |p: P2| [p[0] + at, p[1] + SF_RAISE];
                ink.glyphs.push(
                    laid.contours
                        .into_iter()
                        .map(|c| c.into_iter().map(|s| moved(&s, &shift)).collect())
                        .collect(),
                );
                x += SF_CELL;
            }
            Some(c) => {
                let (l, m, r) = (x + 0.15 * h, x + 0.5 * h, x + 0.85 * h);
                let strokes: Vec<[P2; 2]> = match c {
                    '↧' => vec![
                        [[l, CAP], [r, CAP]],
                        [[m, CAP], [m, 0.0]],
                        [[m - 0.2 * h, 0.25 * h], [m, 0.0]],
                        [[m, 0.0], [m + 0.2 * h, 0.25 * h]],
                    ],
                    '⌴' => vec![
                        [[l, CAP], [l, 0.0]],
                        [[l, 0.0], [r, 0.0]],
                        [[r, 0.0], [r, CAP]],
                    ],
                    _ => vec![[[l, CAP], [m, 0.0]], [[m, 0.0], [r, CAP]]],
                };
                for s in &strokes {
                    grow(&mut ink, s[0], s[1]);
                }
                ink.strokes.extend(strokes);
                x += h;
            }
            None => {
                glyphs(text, x, &mut ink);
                x += font.advance(text, h);
            }
        }
    }
    if !ink.min[0].is_finite() {
        (ink.min, ink.max) = ([0.0; 2], [0.0; 2]);
    }
    ink
}

/// The spotface symbol's cell: the counterbore's box widened from 0.7 to 1.1 text heights so the
/// letters fit inside with a margin.
const SF_CELL: f64 = 1.4 * TEXT;
/// The height the spotface symbol's `SF` is set at, and its baseline above the box's floor: its
/// capitals (0.7 of the size in the bundled mono font) stand clear of the floor and below the
/// box's sides.
const SF_TEXT: f64 = 0.6 * TEXT;
const SF_RAISE: f64 = 0.12 * TEXT;

pub(super) fn moved(s: &Segment, f: &dyn Fn(P2) -> P2) -> Segment {
    match *s {
        Segment::Line(a, b) => Segment::Line(f(a), f(b)),
        Segment::Quad(a, b, c) => Segment::Quad(f(a), f(b), f(c)),
        Segment::Cubic(a, b, c, d) => Segment::Cubic(f(a), f(b), f(c), f(d)),
    }
}

/// A label's frame on the page: its baseline origin and direction.
pub(super) fn to_page(origin: P2, rotation: f64) -> impl Fn(P2) -> P2 {
    let (sin, cos) = rotation.to_radians().sin_cos();
    move |p: P2| {
        [
            origin[0] + cos * p[0] - sin * p[1],
            origin[1] + sin * p[0] + cos * p[1],
        ]
    }
}

pub(super) fn label_box(ink: &LabelInk, origin: P2, rotation: f64) -> [f64; 4] {
    let f = to_page(origin, rotation);
    let corners = [
        f(ink.min),
        f([ink.max[0], ink.min[1]]),
        f(ink.max),
        f([ink.min[0], ink.max[1]]),
    ];
    let mut b = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for c in corners {
        b = [
            b[0].min(c[0]),
            b[1].min(c[1]),
            b[2].max(c[0]),
            b[3].max(c[1]),
        ];
    }
    b
}

pub(super) fn arrow(tip: P2, from: P2) -> [P2; 2] {
    let d = [from[0] - tip[0], from[1] - tip[1]];
    let n = d[0].hypot(d[1]).max(1e-12);
    [tip, [tip[0] + ARROW * d[0] / n, tip[1] + ARROW * d[1] / n]]
}

/// A strip beside a view's outline, where its annotations stack outward in lanes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, serde::Serialize)]
pub(super) enum Side {
    Below,
    Above,
    Left,
    Right,
}

/// The strips draftwright reserves (`layout::Strips`): a view's horizontal dimensions below it,
/// except the plan's, which go above it (its x-location tiers); vertical ones right of the front
/// and the side, and left of the plan (the front/plan column's left band). Callouts take the
/// outermost lanes: below the front, above the plan and the side.
pub(super) fn strip(view: &str, horizontal: bool) -> Side {
    match (view, horizontal) {
        ("plan", true) => Side::Above,
        (_, true) => Side::Below,
        ("plan", false) => Side::Left,
        _ => Side::Right,
    }
}

fn callout_strip(view: &str) -> Side {
    if view == "front" {
        Side::Below
    } else {
        Side::Above
    }
}

/// An inclined span belongs beside its witnesses, rather than beyond the remote end of
/// the whole part. Choose the nearer parallel support of the view's outline; keep the
/// usual strip when both are equally near. Explicit placement still overrides this.
fn aligned_strip(a: P2, b: P2, outline: [f64; 4], preferred: Side) -> Side {
    let mut n = [a[1] - b[1], b[0] - a[0]];
    let outward = match preferred {
        Side::Above => [0.0, 1.0],
        Side::Below => [0.0, -1.0],
        Side::Left => [-1.0, 0.0],
        Side::Right => [1.0, 0.0],
    };
    if n[0] * outward[0] + n[1] * outward[1] < 0.0 {
        n = n.map(|v| -v);
    }
    let distance = (0..2)
        .map(|k| n[k] * (a[k] + b[k] - outline[k] - outline[k + 2]))
        .sum::<f64>();
    if distance < -1e-6 {
        opposite(preferred)
    } else {
        preferred
    }
}

/// The page position of lane *lane* in strip *side*, and the outward sign.
fn lane_at(o: [f64; 4], side: Side, lane: usize) -> (f64, f64) {
    let d = first_line() + lane as f64 * lane_pitch();
    match side {
        Side::Below => (o[1] - d, -1.0),
        Side::Above => (o[3] + d, 1.0),
        Side::Left => (o[0] - d, -1.0),
        Side::Right => (o[2] + d, 1.0),
    }
}

/// Exterior callout elbow rows, in paper mm; keys are the four Spec side names.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct LabelRows {
    pub outline: [f64; 4],
    pub first_row: BTreeMap<String, f64>,
    pub row_spacing: BTreeMap<String, f64>,
}

fn callout_step(side: Side, width: f64, canonical_width: f64) -> f64 {
    if matches!(side, Side::Left | Side::Right) {
        canonical_width.max(width) + super::SHOULDER + lane_pitch()
    } else {
        lane_pitch()
    }
}

/// Rows for a view and the greater of its canonical and edited callout widths.
pub fn label_rows(analysis: &Analysis, view: &PlannedView, width: f64) -> LabelRows {
    let outline = Frame::new(analysis, view).outline();
    let sides = [
        ("above", Side::Above),
        ("below", Side::Below),
        ("left", Side::Left),
        ("right", Side::Right),
    ];
    LabelRows {
        outline,
        first_row: sides
            .iter()
            .map(|(name, side)| (name.to_string(), lane_at(outline, *side, 0).0))
            .collect(),
        row_spacing: sides
            .iter()
            .map(|(name, side)| (name.to_string(), callout_step(*side, width, width)))
            .collect(),
    }
}

/// Whether world axis *axis* runs across the page in this view (else up it); none when the
/// view looks along it.
pub(super) fn across(frame: &Frame, axis: usize) -> Option<bool> {
    let mut e = [0.0; 3];
    e[axis] = 1.0;
    let u = frame.view.map(e);
    if u[0].abs() > 0.5 {
        Some(true)
    } else if u[1].abs() > 0.5 {
        Some(false)
    } else {
        None
    }
}

/// A plate thickness's default strip: Python's fixed axis map, with the front's vertical walls
/// on its left and the side's horizontal walls above it.
pub(super) fn thickness_strip(view: &str, axis: usize, horizontal: bool) -> Side {
    match (view, axis) {
        ("front", 2) => Side::Left,
        ("side", 1) => Side::Above,
        _ => strip(view, horizontal),
    }
}

/// How near (page mm) a point must be to the view's silhouette to lie on it.
pub(super) const ON_SILHOUETTE: f64 = 0.05;

/// How far (page mm) a plate thickness's witnesses may run over the part before leaving it at
/// the silhouette, when no pair of its solid edges reaches the silhouette in any view: about an
/// arrowhead, as where a flange ends just short of a chamfer that forms the outline.
pub(super) const NEAR_SILHOUETTE: f64 = 3.0;

/// The view's silhouette edge nearest page point *p* across a dimension that runs horizontally
/// (*horizontal*) or vertically, and the distance to it: the strip whose extension lines leave
/// the part there.
pub(super) fn silhouette_gap(frame: &Frame, p: P3, horizontal: bool) -> (Side, f64) {
    let o = frame.outline();
    let c = usize::from(horizontal);
    let q = frame.page(p)[c];
    let (low, high) = if horizontal {
        (Side::Below, Side::Above)
    } else {
        (Side::Left, Side::Right)
    };
    let (dl, dh) = ((q - o[c]).abs(), (q - o[c + 2]).abs());
    if dl <= dh { (low, dl) } else { (high, dh) }
}

/// The strip a plate thickness takes: where its witnesses meet the silhouette when they are
/// anchored on solid profile edges ([`super::plates::prefer_readable`]), else its default strip.
fn plate_strip(frame: &Frame, view: &str, shape: &Shape, horizontal: bool) -> Side {
    match shape {
        Shape::Linear {
            a,
            axis,
            a_edge,
            b_edge,
            ..
        } => (!a_edge && !b_edge)
            .then(|| silhouette_gap(frame, *a, horizontal))
            .filter(|(_, d)| *d <= NEAR_SILHOUETTE)
            .map_or_else(
                || thickness_strip(view, *axis, horizontal),
                |(side, _)| side,
            ),
        _ => strip(view, horizontal),
    }
}

/// Extra text clearance belongs to the new automatic slot-group layout;
/// authored PMI and other nominal layouts keep their existing route policy.
pub(super) fn automatic_slot_layout(analysis: &Analysis) -> bool {
    !crate::nominal::comparison()
        && crate::nominal::geometry_eligible(analysis)
        // Recess drawings already use the joint corridor solver; do not
        // replace its coupled witness and callout policy with local retries.
        && !analysis.features.iter().any(|f| f.kind == "section_recess")
        && analysis
            .features
            .iter()
            .any(|f| matches!(f.kind.as_str(), "slot" | "slot_pattern"))
}

/// Physical witness retries also apply to source-authored spans. Only placement changes.
pub(super) fn physical_witness_retry(analysis: &Analysis) -> bool {
    if !crate::nominal::authored_pmi(analysis) {
        return automatic_slot_layout(analysis);
    }
    if crate::nominal::comparison()
        || !analysis
            .recognition
            .as_ref()
            .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "))
        || analysis.features.iter().any(|f| f.kind == "section_recess")
        || !analysis
            .features
            .iter()
            .any(|f| matches!(f.kind.as_str(), "slot" | "slot_pattern"))
    {
        return false;
    }
    // Source layouts with local plates, notches or other coupled profiles
    // retain their joint placement. This local retry is for slots in stock;
    // it must not spend a neighbouring body's reserved witness corridor.
    analysis.features.iter().all(|f| {
        matches!(
            f.kind.as_str(),
            "hole" | "hole_pattern" | "slot" | "slot_pattern" | "fillet" | "chamfer"
        )
    })
}

/// Source-PMI layouts eligible for physical witness retries also give dependent
/// callouts the ordinary corridor choices, including supplementary references.
pub(super) fn pmi_witness_retry(analysis: &Analysis) -> bool {
    if crate::nominal::comparison() {
        return false;
    }
    crate::planning::memo(analysis, "pmi-witness-retry", Vec::new(), || {
        physical_witness_retry(analysis) && crate::nominal::authored_pmi(analysis)
    })
}

/// The strip of a turned step length anchored on its own body's silhouette (both witnesses off
/// the outline: [`super::turned`]'s chains on a part of several turned bodies, ARCH0020-2): the
/// side of the view its witnesses leave the body on, so they never cross a sibling body.
/// Gusset locations and slot lengths use the same outward-silhouette convention.
fn body_strip(frame: &Frame, w: &Want, horizontal: bool, slot_layout: bool) -> Option<Side> {
    match &w.shape {
        Shape::Linear {
            a,
            a_edge: false,
            b_edge: false,
            ..
        } if !w.measurements.is_empty()
            && w.measurements.iter().all(|m| {
                matches!(
                    m.parameter.as_str(),
                    "step.length" | "gusset_location.length"
                ) || (slot_layout
                    && matches!(
                        m.parameter.as_str(),
                        "slot_length.length" | "slot_centres.length"
                    ))
            }) =>
        {
            Some(silhouette_gap(frame, *a, horizontal).0)
        }
        _ => None,
    }
}

/// The strip of a profile corner station ([`super::profile_angles`], #217) whose corner lies on
/// the view's silhouette: that side, so the witness from the corner leaves the part at once
/// rather than crossing it (l-plate-5-holes' two `20`s, whose corners lie on the top and left
/// edges). None for any other item; a `dimensions/<id>` side places it elsewhere.
pub(super) fn station_strip(frame: &Frame, w: &Want, horizontal: bool) -> Option<Side> {
    let Shape::Linear {
        a,
        b,
        a_edge,
        b_edge,
        ..
    } = &w.shape
    else {
        return None;
    };
    if w.measurements.is_empty()
        || !w
            .measurements
            .iter()
            .all(|m| m.parameter.starts_with("profile_location_"))
        || a_edge == b_edge
    {
        return None;
    }
    let corner = if *a_edge { b } else { a };
    let (side, gap) = silhouette_gap(frame, *corner, horizontal);
    (gap <= ON_SILHOUETTE).then_some(side)
}

/// For each item, the item whose lane it shares, when the defaults merge it away: one span, one
/// dimension (ARCH0009-6; [`super::bosses::consolidate`], [`super::holes::consolidate`],
/// [`super::share`]) leaves one dimension on a span that several items measure, so the others
/// take no lane of their own (draftwright's corridor dedup: a displaced duplicate is never
/// placed while its winner is). The merges are decided among every item the part would have,
/// under the defaults' labels, so no spec edit changes them (ARCH0003-2): an item the spec brings
/// back by omitting its survivor is drawn in the survivor's lane, where the span already was.
/// Counted walls on different projected axes keep a parallel strip of their own.
pub(super) fn owners(
    analysis: &Analysis,
    spec: &crate::model::Spec,
    items: &[Want],
    views_of: &[Option<&'static str>],
) -> Vec<Option<usize>> {
    let mut kept: Vec<(Want, bool, Option<&'static str>, Vec<&'static str>)> = items
        .iter()
        .zip(views_of)
        .map(|(w, view)| (w.clone(), true, *view, Vec::new()))
        .collect();
    let mut given = vec![Vec::new(); kept.len()];
    let mut omitted = Vec::new();
    super::plates::consolidate(analysis, &mut kept, &mut omitted);
    super::bosses::consolidate(analysis, &mut kept, &mut omitted);
    super::holes::consolidate(analysis, &mut kept, &mut given, &mut omitted);
    super::recesses::consolidate(&mut kept, &mut given, &mut omitted);
    super::plates::share(analysis, &mut kept, &mut given, &mut omitted);
    super::share(&mut kept, &mut given, &mut omitted);
    super::redundancy::consolidate(analysis, spec, &mut kept, &mut given, &mut omitted);
    let index: BTreeMap<&str, usize> = items
        .iter()
        .enumerate()
        .map(|(i, w)| (w.id.as_str(), i))
        .collect();
    // Covered items name their survivor independently of the explanatory text.
    let mut owner = vec![None; items.len()];
    for o in &omitted {
        let survivor = match &o.state {
            crate::items::State::Covered { by } => index.get(by.as_str()),
            _ => None,
        };
        // An owner outside the linear inventory reserves no shared lane here.
        if let (Some(&i), Some(&s)) = (index.get(o.id.as_str()), survivor) {
            owner[i] = Some(s);
        }
    }
    // A survivor merged away in turn passes on its own survivor.
    for i in 0..owner.len() {
        let mut s = owner[i];
        for _ in 0..owner.len() {
            match s.and_then(|s| owner[s]) {
                Some(next) => s = Some(next),
                None => break,
            }
        }
        owner[i] = s;
    }
    owner
}

/// Prefer the feature's own open side for its transverse sizes and locations.
/// A longitudinal span uses the nearest side of the actual opening instead.
fn through_notch_strip(
    analysis: &Analysis,
    frame: &Frame,
    w: &Want,
    axis: usize,
    horizontal: bool,
) -> Option<Side> {
    let f = w.measurements.iter().find_map(|m| {
        analysis
            .features
            .iter()
            .find(|f| f.id == m.feature && f.kind == "rectangular_through_notch")
    })?;
    let n = crate::lint::rectangular_notches::read(analysis, f).ok()?;
    if axis == n.wide {
        let (a, b) = (frame.page(n.at), frame.page(n.mouth));
        let k = usize::from(horizontal);
        Some(if k == 1 {
            if b[k] > a[k] {
                Side::Above
            } else {
                Side::Below
            }
        } else if b[k] > a[k] {
            Side::Right
        } else {
            Side::Left
        })
    } else {
        Some(silhouette_gap(frame, n.locator(), horizontal).0)
    }
}

/// Each item's strip and lane in its carrying view (none when it has no view, or its view looks
/// along it): per view and strip, sizes nearest, then locations by distance, overall outermost
/// (ARCH0009-5), then callouts by their tips, right to left. An item merged away ([`owners`])
/// takes its survivor's lane number and, when parallel, its strip.
pub(super) fn lanes<'a>(
    analysis: &Analysis,
    frames: &BTreeMap<&str, Frame>,
    items: impl ExactSizeIterator<Item = (&'a Want, Option<&'static str>)>,
    owner: &[Option<usize>],
) -> (Vec<Option<Side>>, Vec<usize>) {
    let items: Vec<_> = items.collect();
    let n = items.len();
    let ordinary = physical_witness_retry(analysis)
        && !analysis
            .features
            .iter()
            .any(|f| f.id.starts_with("f:section_recess:"));
    let slot_views: std::collections::BTreeSet<_> = items
        .iter()
        .filter(|(w, _)| {
            w.measurements.iter().any(|m| {
                matches!(
                    m.parameter.as_str(),
                    "slot_width.length" | "slot_length.length" | "slot_centres.length"
                )
            })
        })
        .filter_map(|(_, view)| *view)
        .collect();
    let mut constrained = vec![false; n];
    let mut footprints = BTreeMap::new();
    let mut strips: Strips = BTreeMap::new();
    let mut sides: Vec<Option<Side>> = vec![None; n];
    for (i, &(w, view)) in items.iter().enumerate() {
        let Some(view) = view else { continue };
        let f = &frames[view];
        let (mut side, rank) = match &w.shape {
            Shape::Linear {
                a, b, axis, order, ..
            } => {
                let Some(horizontal) = across(f, *axis) else {
                    continue;
                };
                (
                    if w.measurements
                        .iter()
                        .any(|m| m.parameter == "thickness.length")
                    {
                        plate_strip(f, view, &w.shape, horizontal)
                    } else if matches!(view, "front" | "side")
                        && horizontal
                        && w.measurements.iter().any(|m| {
                            m.feature.starts_with("notch:") && m.parameter.starts_with("location.")
                        })
                    {
                        Side::Above
                    } else if let Some(side) =
                        through_notch_strip(analysis, f, w, *axis, horizontal)
                    {
                        side
                    } else if let Some(side) =
                        body_strip(f, w, horizontal, physical_witness_retry(analysis))
                    {
                        side
                    } else if w.measurements.iter().any(|m| {
                        m.feature.starts_with("notch:") && m.parameter.starts_with("location.")
                    }) && silhouette_gap(f, *b, horizontal).1 <= ON_SILHOUETTE
                    {
                        // A notch mouth on the silhouette is located on that free side,
                        // keeping its witnesses out of the existing opposite-side callouts.
                        silhouette_gap(f, *b, horizontal).0
                    } else if let Some(side) = station_strip(f, w, horizontal) {
                        side
                    } else {
                        strip(view, horizontal)
                    },
                    (*order, key((b[*axis] - a[*axis]).abs())),
                )
            }
            Shape::Aligned { a, b } => {
                let (a, b) = (f.page(*a), f.page(*b));
                (
                    aligned_strip(
                        a,
                        b,
                        f.outline(),
                        strip(view, (b[0] - a[0]).abs() >= (b[1] - a[1]).abs()),
                    ),
                    (1, key((b[0] - a[0]).hypot(b[1] - a[1]))),
                )
            }
            Shape::Callout { sites, arcs } => {
                let side = callout_strip(view);
                let (tip, _) = callout_tip(f, sites, arcs, side == Side::Above);
                (side, (3, -key(tip[0])))
            }
            // An angle is placed about its vertex, in no strip ([`super::angular`]).
            Shape::Angular { .. } => continue,
        };
        // A slot group must leave the other locations a clear escape. Reserve
        // uniquely clear sides before flexible slot sizes spend their lanes.
        // Recess layouts retain their guarded joint corridor policy.
        if ordinary
            && slot_views.contains(view)
            && matches!(w.shape, Shape::Linear { order: 1, .. })
            && !w.measurements.is_empty()
        {
            let footprint = footprints.entry(view).or_insert_with(|| {
                let key = serde_json::to_vec(&(view, f.memo_geometry())).expect("frame serializes");
                crate::planning::memo(analysis, "lane-location-footprints", key, || {
                    std::rc::Rc::new(super::footprints::Footprints::new(analysis, f, view))
                })
            });
            let counter = footprint.extension_counter(&w.measurements);
            let hits = |side| {
                place(f, w, side, 0).map(|p| {
                    p.lines
                        .iter()
                        .take(2)
                        .map(|l| counter.count(*l))
                        .sum::<usize>()
                })
            };
            let other = opposite(side);
            if let (Some(home), Some(away)) = (hits(side), hits(other)) {
                if away < home {
                    side = other;
                }
                constrained[i] = home.min(away) == 0 && home != away;
            }
        }
        sides[i] = Some(side);
        if owner[i].is_none() {
            strips.entry((view, side)).or_default().push((
                rank.0 >= 2
                    || (!w.measurements.is_empty()
                        && w.measurements
                            .iter()
                            .all(|m| m.feature.starts_with("f:section_recess:"))),
                rank.0,
                rank.1,
                i,
            ));
        }
    }
    // Keep a plane span off the required profile corner it touches. Other source
    // dimensions keep their established strips, including datum baseline ladders.
    let mut corners: BTreeMap<&str, Vec<P3>> = BTreeMap::new();
    for &(w, view) in &items {
        if super::profile_angles::is_profile_angle(w)
            && let (Some(view), Shape::Angular { angle, alternates }) = (view, &w.shape)
        {
            let points = corners.entry(view).or_default();
            points.push(angle.vertex);
            points.extend(alternates.iter().map(|a| a.vertex));
        }
    }
    let local: BTreeMap<_, _> = corners
        .iter()
        .map(|(&view, points)| {
            (
                view,
                super::pmi_dimensions::local_plane_spans(analysis, points),
            )
        })
        .collect();
    for (i, &(w, view)) in items.iter().enumerate() {
        let (Some(view), Some(side), Shape::Linear { a, b, .. }) = (view, sides[i], &w.shape)
        else {
            continue;
        };
        if owner[i].is_some()
            || !local
                .get(view)
                .is_some_and(|spans| w.measurements.iter().any(|m| spans.contains(&m.feature)))
        {
            continue;
        }
        let frame = &frames[view];
        let (a, b) = (frame.page(*a), frame.page(*b));
        let across = usize::from(matches!(side, Side::Above | Side::Below));
        let cost = |side| {
            let edge = lane_at(frame.outline(), side, 0).0;
            (a[across] - edge).abs().max((b[across] - edge).abs())
        };
        let other = opposite(side);
        if cost(other) + 1e-6 < cost(side) {
            let own = strips
                .get_mut(&(view, side))
                .expect("a standalone span has a strip");
            let index = own
                .iter()
                .position(|row| row.3 == i)
                .expect("its strip entry");
            let row = own.remove(index);
            strips.entry((view, other)).or_default().push(row);
            sides[i] = Some(other);
        }
    }
    let mut lane = vec![0usize; n];
    for ((view, _), list) in &mut strips {
        // Equal independent PMI spans follow their stations, not STEP entity
        // order. In particular, the upper short span takes the outer lane: its
        // outside label then leaves the above-view control-frame strip clear.
        // Preserve category, length and the established automatic-item order.
        list.sort_by_key(|&(outer, order, length, i)| {
            let w = items[i].0;
            let source = w.id.starts_with("d:pmi:");
            let station = if source
                && let Shape::Linear { a, b, axis, .. } = w.shape
                && let Some(horizontal) = across(&frames[view], axis)
            {
                let along = usize::from(!horizontal);
                key(frames[view].page(a)[along].min(frames[view].page(b)[along]))
            } else {
                0
            };
            (outer, !constrained[i], order, length, source, station, i)
        });
        let links: Vec<_> = list.iter().map(|(_, _, _, i)| items[*i].0).collect();
        for ((_, _, _, i), n) in list.iter().zip(chain_lanes(&frames[view], &links)) {
            lane[*i] = n;
        }
    }
    share_lanes(owner, &mut sides, &mut lane);
    (sides, lane)
}

/// Connected, nonoverlapping linear links share a dimension line when their labels fit
/// between the arrows. Baselines overlap and keep separate lanes. Short links retain their
/// own lane so their outside arrows/text cannot run into the next link. This is geometric,
/// so authored and automatic chains use the same rule, at the actual drawing scale.
fn chain_lanes(frame: &Frame, items: &[&Want]) -> Vec<usize> {
    let spans: Vec<_> = items
        .iter()
        .map(|w| {
            let Shape::Linear {
                a, b, axis, order, ..
            } = w.shape
            else {
                return None;
            };
            let horizontal = across(frame, axis)?;
            let along = usize::from(!horizontal);
            let length = (frame.page(b)[along] - frame.page(a)[along]).abs();
            let ink = label_ink(&w.terms, font());
            (length >= ink.max[0] - ink.min[0] + 2.0 * ARROW + 2.0).then_some((
                axis,
                order,
                a[axis].min(b[axis]),
                a[axis].max(b[axis]),
            ))
        })
        .collect();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for i in 0..items.len() {
        let mut group = vec![i];
        let mut j = 0;
        while j < groups.len() {
            let compatible = |&a: &usize, &b: &usize| match (spans[a], spans[b]) {
                (Some((ka, oa, la, ha)), Some((kb, ob, lb, hb))) => {
                    ka == kb && oa == ob && ha.min(hb) - la.max(lb) <= 1e-6
                }
                _ => false,
            };
            let touches = |&a: &usize, &b: &usize| match (spans[a], spans[b]) {
                (Some((_, _, la, ha)), Some((_, _, lb, hb))) => {
                    (ha - lb).abs() < 1e-6 || (hb - la).abs() < 1e-6
                }
                _ => false,
            };
            if group
                .iter()
                .all(|a| groups[j].iter().all(|b| compatible(a, b)))
                && group
                    .iter()
                    .any(|a| groups[j].iter().any(|b| touches(a, b)))
            {
                group.extend(groups.remove(j));
                // A newly joined link can bridge to an earlier group.
                j = 0;
            } else {
                j += 1;
            }
        }
        groups.push(group);
    }
    // Preserve the original priority of the first link in each chain.
    // Invariant: Groups are created by pushing a member into each new group.
    groups.sort_by_key(|g| *g.iter().min().unwrap());
    let mut lanes = vec![0; items.len()];
    for (lane, group) in groups.iter().enumerate() {
        for &i in group {
            lanes[i] = lane;
        }
    }
    lanes
}

/// Gives each item merged away its survivor's lane number and a parallel strip.
/// Equal walls on different projected axes keep their own strip orientation.
pub(super) fn share_lanes(
    owner: &[Option<usize>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
) {
    for (i, s) in owner.iter().enumerate() {
        if let Some(s) = *s {
            if let (Some(a), Some(b)) = (sides[i], sides[s])
                && matches!(a, Side::Above | Side::Below) == matches!(b, Side::Above | Side::Below)
            {
                sides[i] = Some(b);
            }
            lanes[i] = lanes[s];
        }
    }
}

/// Gives an item merged away under the defaults' labels ([`owners`]) that the plan draws beside
/// the one whose lane it shares (the spec's labels broke the merge: a tolerance on one of them,
/// say) the next free lane of its strip, outside every lane the strip holds, so two drawn items
/// never share a lane; every other item keeps its lane (ARCH0003-2). Items with lanes of their
/// own claim theirs first, then merged items in order. True for each item moved.
pub(super) fn separate(
    owner: &[Option<usize>],
    drawn: &[bool],
    views_of: &[Option<&'static str>],
    sides: &[Option<Side>],
    lanes: &mut [usize],
) -> Vec<bool> {
    let strip = |i: usize| views_of[i].zip(sides[i]);
    let mut next: BTreeMap<(&str, Side), usize> = BTreeMap::new();
    for (i, lane) in lanes.iter().enumerate() {
        if let Some(key) = strip(i) {
            let n = next.entry(key).or_default();
            *n = (*n).max(lane + 1);
        }
    }
    let mut claimed = std::collections::BTreeSet::new();
    for (i, lane) in lanes.iter().enumerate() {
        if let (Some(key), None, true) = (strip(i), owner[i], drawn[i]) {
            claimed.insert((key, *lane));
        }
    }
    let mut moved = vec![false; lanes.len()];
    for i in 0..lanes.len() {
        let (Some(key), Some(_), true) = (strip(i), owner[i], drawn[i]) else {
            continue;
        };
        if !claimed.insert((key, lanes[i])) {
            // Invariant: next was initialized for every key in the same strips collection.
            let n = next.get_mut(&key).expect("every strip has its next lane");
            lanes[i] = *n;
            *n += 1;
            moved[i] = true;
        }
    }
    moved
}

/// The lanes each strip holds (one more than its outermost item's lane), from each item's view,
/// strip and lane, as the layout's bands name them. A strip [`strip`] and [`callout_strip`] never
/// give (above the front, below the plan, left of the front or the side, right of the plan) is a
/// bug.
pub(super) fn band_lanes<'a>(
    items: impl Iterator<Item = ((&'a Option<&'static str>, Option<Side>), usize)>,
) -> Lanes {
    let mut out = Lanes::default();
    for ((view, side), lane) in items {
        let (Some(view), Some(side)) = (view, side) else {
            continue;
        };
        let band = match (*view, side) {
            ("front", Side::Below) => &mut out.front_below,
            ("front", Side::Right) => &mut out.front_right,
            ("plan", Side::Above) => &mut out.plan_above,
            ("plan", Side::Left) => &mut out.plan_left,
            ("front", Side::Left) => &mut out.plate_front_left,
            ("front", Side::Above) => &mut out.plate_front_above,
            ("plan", Side::Right) => &mut out.plate_plan_right,
            ("plan", Side::Below) => &mut out.plate_plan_below,
            ("side", Side::Left) => &mut out.plate_side_left_lanes,
            ("side", Side::Below) => &mut out.side_below,
            ("side", Side::Above) => &mut out.side_above,
            ("side", Side::Right) => &mut out.side_right,
            // Invariant: Band construction admits only the enumerated linear/angle shapes.
            other => unreachable!("no band holds {other:?}"),
        };
        *band = (*band).max(lane + 1);
    }
    out
}

/// The strip across a view from *side*.
fn opposite(side: Side) -> Side {
    match side {
        Side::Below => Side::Above,
        Side::Above => Side::Below,
        Side::Left => Side::Right,
        Side::Right => Side::Left,
    }
}

/// A strip that cannot hold its lanes on the page passes them to the opposite strip
/// (draftwright's fall-through, ISSUE-1215-e: a dimension its strip has no room for retries on the
/// strip across the view). A strip holds its linear dimensions up to the first lane whose item
/// does not fit the page ([`Page::fits`]: on the sheet, its label off the views and the title
/// block, no line through the title block or another view); that item and every one outside it
/// move to the next free lanes of the opposite strip, an overall extent first (draftwright moves
/// only the extent), each that fits there taking the next lane; the extent then goes outside the
/// rest where that still fits. One that fits on neither side shares a lane already held where it
/// runs clear of that lane's items, and otherwise keeps its own lane, where the collision checks
/// report it: nothing is dropped. Callouts take no lanes ([`super::leaders`] places them), and an
/// item merged away follows its survivor ([`owners`]). The strips' lanes come from every item the
/// part would have, and the page from the sheet and the views, which no omission changes, so
/// where an item falls is edit-local (ARCH0003-2).
#[allow(clippy::too_many_arguments)]
pub(super) fn fall_through(
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    owner: &[Option<usize>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
) {
    let page = Page::new(analysis, sheet, views);
    let linear = |i: usize| matches!(items[i].shape, Shape::Linear { .. } | Shape::Aligned { .. });
    let mut strips: BTreeMap<(&str, Side), Vec<usize>> = BTreeMap::new();
    // Callouts hold no lanes: their leaders are placed around the dimensions afterwards.
    for i in 0..items.len() {
        if owner[i].is_some() {
            continue;
        }
        if let (Some(view), Some(side), true) = (views_of[i], sides[i], linear(i)) {
            strips.entry((view, side)).or_default().push(i);
        }
    }
    let fits = |i: usize, side: Side, lane: usize| {
        // Invariant: strips includes only routed items with a selected view.
        let view = views_of[i].expect("a strip's item has a view");
        place(&frames[view], &items[i], side, lane).is_some_and(|p| page.fits(&p, view))
    };
    // Each strip's overflow, and the lanes it keeps.
    let mut overflow: Vec<((&str, Side), Vec<usize>)> = Vec::new();
    let mut held: BTreeMap<(&str, Side), usize> = BTreeMap::new();
    // A dimension's extent along its strip, padded by a millimetre, where it would be drawn.
    let reach = |i: usize, side: Side, lane: usize| {
        let view = views_of[i]?;
        let p = place(&frames[view], &items[i], side, lane)?;
        let k = usize::from(p.rotation != 0.0);
        let tips = p.arrows.iter().map(|a| a[0][k]);
        let lo = tips.clone().fold(p.label_box[k], f64::min);
        let hi = tips.fold(p.label_box[k + 2], f64::max);
        Some((lo - 1.0, hi + 1.0))
    };
    for (key, list) in &mut strips {
        list.sort_by_key(|&i| lanes[i]);
        // A strip that would overflow is first packed: in lane order each dimension takes the
        // innermost lane outside everything it overlaps, sharing one with what it does not. The
        // strip keeps that packing where every item then fits, rather than sending items across
        // the view that a closed-up strip holds ([`super::compact_inventory`]).
        if list.iter().any(|&i| !fits(i, key.1, lanes[i])) {
            let spans: Vec<_> = list.iter().map(|&i| reach(i, key.1, lanes[i])).collect();
            let mut packed = vec![0usize; list.len()];
            for n in 0..list.len() {
                packed[n] = (0..n)
                    .filter(|&m| match (spans[m], spans[n]) {
                        (Some((lo, hi)), Some((a, b))) => hi > a && b > lo,
                        _ => true,
                    })
                    .map(|m| packed[m] + 1)
                    .max()
                    .unwrap_or(0);
            }
            if list
                .iter()
                .zip(&packed)
                .all(|(&i, &lane)| fits(i, key.1, lane))
            {
                for (&i, &lane) in list.iter().zip(&packed) {
                    lanes[i] = lane;
                }
            }
        }
        list.sort_by_key(|&i| lanes[i]);
        let cut = list
            .iter()
            .position(|&i| !fits(i, key.1, lanes[i]))
            .unwrap_or(list.len());
        // A member another item is merged into keeps the established fall-through: an item the
        // spec brings back is drawn in its survivor's lane, where the span already was, and a
        // whole baseline moving across would move the drawing around it (ARCH0003-2).
        let out: Vec<usize> = list[cut..].to_vec();
        let lanes_held = list[..cut].iter().map(|&i| lanes[i] + 1).max();
        held.insert(*key, lanes_held.unwrap_or(0));
        if !out.is_empty() {
            overflow.push((*key, out));
        }
    }
    let full: Vec<(&str, Side)> = overflow.iter().map(|(key, _)| *key).collect();
    // What fits on neither side, in strip order.
    let mut stranded: Vec<usize> = Vec::new();
    for ((view, side), list) in overflow {
        let across = opposite(side);
        // An opposite strip that overflows itself has no room: what it could not hold keeps its
        // lanes there.
        if full.contains(&(view, across)) {
            stranded.extend(list);
            continue;
        }
        let first = *held.get(&(view, across)).unwrap_or(&0);
        // A strip left holding only the innermost link of one baseline (dimensions from the
        // same face along the same axis) whose other links went across gives it across too,
        // innermost, where the whole baseline fits there: one baseline reads on one side, and a
        // short inner link does not run its extension lines across the view alone
        // (nema17-bracket's `5` crossed a slot and three holes on the left, one slot on the right).
        // A link crossing no more features where it is stays (mounting_plate's `15`, whose move
        // cost the A4 sheet a leader on an extension line), and so does more than one link
        // (cgb243's seven hole heights, reshuffled, lost more than they gained).
        let datum = |i: usize| match items[i].shape {
            Shape::Linear {
                a,
                axis,
                a_edge: true,
                ..
            } => Some((axis, key(a[axis]))),
            _ => None,
        };
        let mut kept: Vec<usize> = (0..items.len())
            .filter(|&i| {
                owner[i].is_none()
                    && linear(i)
                    && views_of[i] == Some(view)
                    && sides[i] == Some(side)
                    && !list.contains(&i)
            })
            .collect();
        kept.sort_by_key(|&i| lanes[i]);
        let mut list = list;
        if kept.len() == 1
            && kept
                .iter()
                .all(|&i| datum(i).is_some() && list.iter().any(|&j| datum(j) == datum(i)))
            && list
                .iter()
                .filter(|&&j| datum(j).is_some() && kept.iter().any(|&i| datum(i) == datum(j)))
                .count()
                > kept.len()
            && kept
                .iter()
                .chain(&list)
                .enumerate()
                .all(|(k, &i)| fits(i, across, first + k))
            && {
                // Only for a link whose extension lines cross fewer features there.
                let i = kept[0];
                let footprints = super::footprints::Footprints::new(analysis, &frames[view], view);
                let crossed = |side: Side, lane: usize| {
                    place(&frames[view], &items[i], side, lane).map(|p| {
                        p.lines
                            .iter()
                            .take(2)
                            .map(|s| {
                                footprints
                                    .crossed_extension(*s, &items[i].measurements)
                                    .len()
                            })
                            .sum::<usize>()
                    })
                };
                matches!(
                    (crossed(side, lanes[i]), crossed(across, first)),
                    (Some(home), Some(there)) if there < home
                )
            }
        {
            kept.extend(list);
            list = kept;
            held.insert((view, side), 0);
        }
        // An overall extent has first claim on the free lanes (draftwright falls through only
        // the extent, ISSUE-1215-e); the others follow in their order.
        let extent = |i: usize| crate::dims::is_envelope_extent(&items[i].id);
        let (mut order, rest): (Vec<usize>, Vec<usize>) =
            list.into_iter().partition(|&i| extent(i));
        order.extend(rest);
        let mut moved = Vec::new();
        for &i in &order {
            if fits(i, across, first + moved.len()) {
                moved.push(i);
            }
        }
        // The extent then stands outside the rest, as it did on its own strip, where that fits.
        let mut outer: Vec<usize> = moved.iter().copied().filter(|&i| !extent(i)).collect();
        outer.extend(moved.iter().copied().filter(|&i| extent(i)));
        if outer != moved
            && outer
                .iter()
                .enumerate()
                .all(|(k, &i)| fits(i, across, first + k))
        {
            moved = outer;
        }
        for (k, &i) in moved.iter().enumerate() {
            sides[i] = Some(across);
            lanes[i] = first + k;
        }
        held.insert((view, across), first + moved.len());
        stranded.extend(order.into_iter().filter(|i| !moved.contains(i)));
    }
    // One that fits on neither side then shares a lane either strip already holds, where it fits
    // the page and runs clear of every item in that lane along it (draftwright's lateral tier
    // reuse, `_adjust_strip_candidate_labels`: a dimension shares an inner tier where its whole
    // extent is disjoint from every occupant there). A slot's location so chains with the slot's
    // own length, end to end on one line. No lane is added. The item with the fewest such lanes
    // takes one first (the longest among as few, then strip order), its own strip's innermost
    // first, so one lane is not spent where another would have done.
    share_held(
        frames, items, views_of, owner, sides, lanes, &held, &stranded, &fits,
    );
    // A slot's datum leg and its own length read as one end-to-end chain when
    // their complete ink is disjoint. Reserve that local corridor on every
    // drawing, rather than making it depend on the presence of recesses.
    if automatic_slot_layout(analysis)
        || items.iter().any(|w| {
            w.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        })
    {
        for i in 0..items.len() {
            let Some(feature) = items[i].id.strip_suffix("/location_slot.length") else {
                continue;
            };
            if owner[i].is_some() {
                continue;
            }
            let length = format!("{feature}/slot_length.length");
            let Some(mut j) = items.iter().position(|w| w.id == length) else {
                continue;
            };
            if let Some(survivor) = owner[j] {
                j = survivor;
            }
            let (Some(view), Some(side)) = (views_of[j], sides[j]) else {
                continue;
            };
            if views_of[i] != Some(view) {
                continue;
            }
            let (Some(p), Some(q)) = (
                place(&frames[view], &items[i], side, lanes[j]),
                place(&frames[view], &items[j], side, lanes[j]),
            ) else {
                continue;
            };
            if !page.fits(&p, view) {
                continue;
            }
            let k = usize::from(matches!(side, Side::Left | Side::Right));
            let ends = |p: &Placed| {
                let (a, b) = (p.arrows[0][0][k], p.arrows[1][0][k]);
                (a.min(b), a.max(b))
            };
            let ((a, b), (c, d)) = (ends(&p), ends(&q));
            if (b - c).abs().min((d - a).abs()) > 1e-6 {
                continue;
            }
            let span = |p: &Placed| {
                ink_points(p).fold([f64::INFINITY, f64::NEG_INFINITY], |e, p| {
                    [e[0].min(p[k]), e[1].max(p[k])]
                })
            };
            let e = span(&p);
            let clear = (0..items.len())
                .filter(|&t| {
                    t != i
                        && owner[t].is_none()
                        && views_of[t] == Some(view)
                        && sides[t] == Some(side)
                        && lanes[t] == lanes[j]
                })
                .filter_map(|t| place(&frames[view], &items[t], side, lanes[t]))
                .all(|q| {
                    let f = span(&q);
                    e[1] <= f[0] + 1e-6 || f[1] <= e[0] + 1e-6
                });
            if clear {
                sides[i] = Some(side);
                lanes[i] = lanes[j];
            }
        }
    }

    share_lanes(owner, sides, lanes);
}

/// The stranded items' shared lanes ([`fall_through`]): each lane a strip holds (*held*) that
/// *fits* the item on the page and whose items' extents along it are disjoint from the item's
/// (ends may meet).
#[allow(clippy::too_many_arguments)]
fn share_held(
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    owner: &[Option<usize>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
    held: &BTreeMap<(&str, Side), usize>,
    stranded: &[usize],
    fits: &dyn Fn(usize, Side, usize) -> bool,
) {
    if stranded.is_empty() {
        return;
    }
    let along = |i: usize, side: Side, lane: usize| {
        // Invariant: strips includes only routed items with a selected view.
        let view = views_of[i].expect("a strip's item has a view");
        let k = usize::from(matches!(side, Side::Left | Side::Right));
        place(&frames[view], &items[i], side, lane).map(|p| {
            ink_points(&p).fold([f64::INFINITY, f64::NEG_INFINITY], |e, q| {
                [e[0].min(q[k]), e[1].max(q[k])]
            })
        })
    };
    // Each held lane's items' extents along it.
    let mut taken: BTreeMap<(&str, Side, usize), Vec<[f64; 2]>> = BTreeMap::new();
    for j in 0..items.len() {
        if let (Some(view), Some(side), None, Shape::Linear { .. } | Shape::Aligned { .. }) =
            (views_of[j], sides[j], owner[j], &items[j].shape)
            && lanes[j] < *held.get(&(view, side)).unwrap_or(&0)
            && !stranded.contains(&j)
            && let Some(e) = along(j, side, lanes[j])
        {
            taken.entry((view, side, lanes[j])).or_default().push(e);
        }
    }
    // Each stranded item's places: a held lane of its own strip or the opposite one, innermost
    // first, that it fits, with its extent there.
    let mut places: Vec<(usize, Vec<Shared>)> = stranded
        .iter()
        .map(|&i| {
            // Invariant: strips includes only routed items with a selected view.
            let view = views_of[i].expect("a strip's item has a view");
            // Invariant: strips includes only routed items with a selected side.
            let own = sides[i].expect("a strip's item has a side");
            let list = [own, opposite(own)]
                .into_iter()
                .flat_map(|side| {
                    (0..*held.get(&(view, side)).unwrap_or(&0)).map(move |lane| (side, lane))
                })
                .filter(|&(side, lane)| fits(i, side, lane))
                .filter_map(|(side, lane)| along(i, side, lane).map(|e| (side, lane, e)))
                .collect();
            (i, list)
        })
        .collect();
    let clear = |taken: &BTreeMap<(&str, Side, usize), Vec<[f64; 2]>>,
                 view: &str,
                 (side, lane, e): Shared| {
        taken.get(&(view, side, lane)).is_none_or(|list| {
            list.iter()
                .all(|o| e[1] <= o[0] + 1e-6 || o[1] <= e[0] + 1e-6)
        })
    };
    loop {
        // The item with the fewest clear places, the longest among as few (first fit
        // decreasing), then first in strip order.
        let best = places
            .iter()
            .enumerate()
            .filter_map(|(k, (i, list))| {
                // Invariant: strips includes only routed items with a selected view.
                let view = views_of[*i].expect("a strip's item has a view");
                let mut open = list.iter().filter(|&&p| clear(&taken, view, p));
                let first = open.next()?;
                let n = 1 + open.count();
                Some((n, -key(first.2[1] - first.2[0]), k))
            })
            .min();
        let Some((.., k)) = best else { break };
        let (i, list) = places.remove(k);
        // Invariant: strips includes only routed items with a selected view.
        let view = views_of[i].expect("a strip's item has a view");
        let (side, lane, e) = *list
            .iter()
            .find(|&&p| clear(&taken, view, p))
            // Invariant: The preceding search tested this same selected candidate as clear.
            .expect("a clear place");
        sides[i] = Some(side);
        lanes[i] = lane;
        taken.entry((view, side, lane)).or_default().push(e);
    }
}

/// Body-local turned chains use the corridor next to their own silhouette, including a
/// middle body. Try both sides before keeping and reporting a constrained chain (Policy B).
pub(super) fn body_chains(
    analysis: &Analysis,
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    carrying: &[Option<&str>],
    fixed: &[bool],
    placed: &mut [Option<Placed>],
    page: &Page,
) -> Vec<Problem> {
    let fr = crate::bridge::holes::frame(analysis);
    let bands: Vec<_> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .filter_map(|f| {
            crate::lint::turned::band(&fr, f)
                .ok()
                .map(|b| (f.id.as_str(), b))
        })
        .collect();
    let same = |a: &crate::lint::turned::Band, b: &crate::lint::turned::Band| {
        a.axis == b.axis && (0..3).all(|k| k == a.axis || (a.on[k] - b.on[k]).abs() <= 1e-3)
    };
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (i, w) in items.iter().enumerate() {
        if w.measurements.is_empty() || !w.measurements.iter().all(|m| m.parameter == "step.length")
        {
            continue;
        }
        let Some((_, b)) = bands
            .iter()
            .find(|(id, _)| *id == w.measurements[0].feature)
        else {
            continue;
        };
        if !bands.iter().any(|(_, c)| c.axis == b.axis && !same(b, c)) {
            continue;
        }
        if let Some(group) = groups.iter_mut().find(|g| {
            let feature = &items[g[0]].measurements[0].feature;
            bands
                .iter()
                .find(|(id, _)| *id == feature)
                .is_some_and(|(_, c)| same(b, c))
        }) {
            group.push(i);
        } else {
            groups.push(vec![i]);
        }
    }
    let mut problems = Vec::new();
    for group in groups {
        let first = group[0];
        let Some(view) = carrying[first] else {
            continue;
        };
        let f = &frames[view];
        let (_, b) = bands
            .iter()
            .find(|(id, _)| *id == items[first].measurements[0].feature)
            // Invariant: Bands are constructed only after their feature grammar has been read successfully.
            .expect("read band");
        let k = b.axis;
        let Some(horizontal) = across(f, k) else {
            continue;
        };
        let j = (0..3)
            .find(|&j| j != k && across(f, j) == Some(!horizontal))
            // Invariant: Only profile views selected by routing enter this branch.
            .expect("a profile view");
        let others: Vec<_> = bands
            .iter()
            .filter(|(_, c)| c.axis == k && !same(b, c))
            .collect();
        if others
            .iter()
            .any(|(_, c)| (c.on[j] - b.on[j]).abs() <= 1e-3 && b.lo <= c.hi && c.lo <= b.hi)
        {
            let alternate = super::envelope::profile_views(k)
                .into_iter()
                .find(|v| *v != view)
                .unwrap_or(view);
            // With both longitudinal views present, another body's chain can be carried
            // by the other view. A restoration fix is meaningful only for an absent view.
            if frames.contains_key(alternate) {
                continue;
            }
            problems.push(Problem{id:format!("p:turned_profile_superimposed:{}",items[first].id),code:"turned_profile_superimposed".into(),severity:"warning".into(),
                message:format!("The turned profile on axis {} at {:?} is superimposed on another profile in the {view} view. Restore the {alternate} view to separate its chain.", ["X","Y","Z"][k],b.on),
                subjects:group.iter().map(|&i|items[i].id.clone()).collect(),
                fix:vec![crate::items::Op::set(format!("views/{alternate}/omit"),serde_json::json!(false))],..Default::default()});
            continue;
        }
        // Preserve authored pins; every unconstrained body chain is a whole assignment.
        if group.iter().any(|&i| fixed[i]) {
            continue;
        }
        let radius = bands
            .iter()
            .filter(|(_, c)| same(b, c))
            .map(|(_, c)| c.diameter / 2.)
            .fold(0.0f64, f64::max);
        let centre = f.page(b.on);
        let cross_axis = usize::from(horizontal);
        let screen_scale = {
            let mut p = b.on;
            p[j] += 1.;
            f.page(p)[cross_axis] - centre[cross_axis]
        };
        let radial = radius * f.s;
        let preferred = if centre[cross_axis] - f.outline[cross_axis]
            <= f.outline[cross_axis + 2] - centre[cross_axis]
        {
            -1.
        } else {
            1.
        };
        let mut best = None;
        let mut assigned = false;
        for sign in [preferred, -preferred] {
            let boundary = others
                .iter()
                .filter_map(|(_, c)| {
                    let q = f.page(c.on)[cross_axis];
                    ((q - centre[cross_axis]) * sign > 0.)
                        .then_some(q - sign * c.diameter * 0.5 * f.s)
                })
                .min_by(|a, b| {
                    ((a - centre[cross_axis]) * sign).total_cmp(&((b - centre[cross_axis]) * sign))
                });
            let side = match (horizontal, sign < 0.) {
                (true, true) => Side::Below,
                (true, false) => Side::Above,
                (false, true) => Side::Left,
                (false, false) => Side::Right,
            };
            let mut candidates = Vec::new();
            let mut clear = true;
            let links: Vec<_> = group.iter().map(|&i| &items[i]).collect();
            for (&i, lane) in group.iter().zip(chain_lanes(f, &links)) {
                let mut w = items[i].clone();
                if let Shape::Linear {
                    a,
                    b: tip,
                    a_edge,
                    b_edge,
                    ..
                } = &mut w.shape
                {
                    // Keep the physical shoulder radius selected by own_body; reflect it about
                    // the axis when the chain falls through to the other side.
                    a[j] = b.on[j] + sign * screen_scale.signum() * (a[j] - b.on[j]).abs();
                    tip[j] = b.on[j] + sign * screen_scale.signum() * (tip[j] - b.on[j]).abs();
                    *a_edge = false;
                    *b_edge = false;
                }
                let line = centre[cross_axis]
                    + sign * (radial + first_line() + lane as f64 * lane_pitch());
                let Some(p) = place_linear_at(f, &w.shape, &w.terms, side, (line, sign)) else {
                    clear = false;
                    continue;
                };
                let bounds = p.label_box;
                let toward = if sign < 0. {
                    bounds[cross_axis]
                } else {
                    bounds[cross_axis + 2]
                };
                if boundary.is_some_and(|edge| (edge - toward) * sign < 1.)
                    || !page.fits_interior(&p, view)
                {
                    clear = false;
                }
                candidates.push((i, p));
            }
            if best.is_none() {
                best = Some(candidates.clone());
            }
            if clear {
                best = Some(candidates);
                assigned = true;
                break;
            }
        }
        if !assigned {
            problems.push(Problem{id:format!("p:turned_chain_no_clear_lane:{}",items[first].id),code:"turned_chain_no_clear_lane".into(),severity:"warning".into(),
                message:"Neither side of this body's silhouette has room for its complete step chain; the chain is retained and its contacts are reported.".into(),
                subjects:group.iter().map(|&i|items[i].id.clone()).collect(),..Default::default()});
        }
        if let Some(candidates) = best {
            for (i, p) in candidates {
                placed[i] = Some(p);
            }
        }
    }
    problems
}

/// Places *w* in lane *lane* of strip *side* of its view; none for a callout.
pub(super) fn place(frame: &Frame, w: &Want, side: Side, lane: usize) -> Option<Placed> {
    match &w.shape {
        Shape::Linear { .. } => {
            if w.measurements
                .iter()
                .any(|m| m.parameter == "thickness.length")
                && let Shape::Linear {
                    a,
                    b,
                    axis,
                    a_edge: false,
                    b_edge: false,
                    ..
                } = &w.shape
                && let Some(horizontal) = across(frame, *axis)
                && [*a, *b]
                    .iter()
                    .any(|p| silhouette_gap(frame, *p, horizontal).0 != side)
            {
                // An exposed profile selects real edge endpoints. A different
                // strip needs another proven edge; reversing the witnesses from
                // the same endpoints would dimension back through the part.
                return None;
            }
            let mut p = place_linear(frame, &w.shape, &w.terms, side, lane)?;
            if w.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
            {
                let k = usize::from(p.rotation != 0.0);
                let hi = p
                    .arrows
                    .iter()
                    .map(|a| a[0][k])
                    .fold(f64::NEG_INFINITY, f64::max);
                if p.label_box[k] > hi {
                    // Outside text belongs immediately beside its own arrow, before
                    // another dimension's witness can intervene.
                    p.label_at[k] -= 2.0;
                    p.label_box[k] -= 2.0;
                    p.label_box[k + 2] -= 2.0;
                }
            }
            Some(p)
        }
        Shape::Aligned { a, b } => place_aligned(frame, *a, *b, &w.terms, side, lane),
        // A callout's leader is placed with the others' ([`super::leaders`]), an angle about its
        // vertex ([`super::angular`]).
        Shape::Callout { .. } | Shape::Angular { .. } => None,
    }
}

/// Places a linear dimension in lane *lane* of strip *side* of its view.
fn place_linear(
    frame: &Frame,
    shape: &Shape,
    terms: &[String],
    side: Side,
    lane: usize,
) -> Option<Placed> {
    place_linear_at(
        frame,
        shape,
        terms,
        side,
        lane_at(frame.outline, side, lane),
    )
}

pub(super) fn interior_lanes(frame: &Frame, w: &Want, side: Side) -> Vec<Placed> {
    let o = frame.outline();
    let (edge, inward) = match side {
        Side::Below => (o[1], 1.0),
        Side::Above => (o[3], -1.0),
        Side::Left => (o[0], 1.0),
        Side::Right => (o[2], -1.0),
    };
    let mut candidates: Vec<_> = (1..=8)
        .filter_map(|lane| {
            let station = edge + inward * lane as f64 * lane_pitch();
            place_linear_at(frame, &w.shape, &w.terms, side, (station, inward))
        })
        .collect();
    // The view's regular lane grid can miss the narrow clear band beside a
    // feature. Try a fixed number of compact offsets from its own witnesses.
    if let Shape::Linear {
        a,
        b,
        a_edge: false,
        b_edge: false,
        ..
    } = w.shape
    {
        let k = usize::from(matches!(side, Side::Above | Side::Below));
        let (a, b) = (frame.page(a)[k], frame.page(b)[k]);
        if (a - b).abs() < 1e-6 {
            let out = if matches!(side, Side::Above | Side::Right) {
                1.
            } else {
                -1.
            };
            // Six one-millimetre offsets from one text height leave room
            // for a label beside a curved end without enlarging the lane grid.
            for gap in (0..6).map(|n| super::TEXT + f64::from(n)) {
                if let Some(p) =
                    place_linear_at(frame, &w.shape, &w.terms, side, (a + out * gap, out))
                {
                    candidates.push(p);
                }
            }
        }
    }
    candidates
}

/// Selected-sheet refinement of opening locations, after paper and view search.
/// The regular seven-mm grid can miss a clear band beside the silhouette.
/// Callers retain the full geometry, text-clearance and printed-ink checks.
pub(super) fn opening_lanes(frame: &Frame, w: &Want, side: Side) -> Vec<Placed> {
    if !matches!(
        w.shape,
        Shape::Linear { a_edge: true, .. } | Shape::Linear { b_edge: true, .. }
    ) {
        return Vec::new();
    }
    let o = frame.outline();
    let (edge, out) = match side {
        Side::Below => (o[1], -1.),
        Side::Above => (o[3], 1.),
        Side::Left => (o[0], -1.),
        Side::Right => (o[2], 1.),
    };
    (0..=28)
        .filter_map(|n| {
            let gap = super::TEXT + super::leaders::STROKE_PAD + 0.25 * f64::from(n);
            place_linear_at(frame, &w.shape, &w.terms, side, (edge + out * gap, out))
        })
        .collect()
}

pub(super) fn linear_pair_contact(a: &Placed, b: &Placed, allow_transverse: bool) -> bool {
    boxes_overlap(a.label_box, b.label_box)
        || a.lines.iter().any(|l| crosses(*l, b.label_box))
        || b.lines.iter().any(|l| crosses(*l, a.label_box))
        || a.arrows
            .iter()
            .flat_map(|h| super::leaders::head(*h))
            .any(|l| crosses(l, b.label_box))
        || b.arrows
            .iter()
            .flat_map(|h| super::leaders::head(*h))
            .any(|l| crosses(l, a.label_box))
        || (a.arrows.len() == 2
            && a.lines.len() >= 3
            && b.arrows.len() == 1
            && !(allow_transverse)
            && super::leaders::meets_extensions(&a.lines[..2], &b.lines, b.label_box))
        || (a.arrows.len() == 2
            && a.lines.len() >= 3
            && b.arrows.len() == 1
            && a.lines[..2].iter().any(|e| {
                b.arrows
                    .iter()
                    .flat_map(|h| super::leaders::head(*h))
                    .any(|h| touches_witness(h, *e, 0.2))
            }))
        || (a.arrows.len() == 2
            && b.arrows.len() == 1
            && allow_transverse
            && a.lines
                .iter()
                .take(2)
                .any(|e| super::leaders::runs_beside(*e, &b.lines)))
        || (a.arrows.len() == 2 && b.arrows.len() == 2 && shared_dimension_line(&a.lines, &b.lines))
}

fn linear_witness_contact(a: &Placed, b: &Placed, allow_transverse: bool) -> bool {
    a.lines.iter().take(2).any(|l| crosses(*l, b.label_box))
        || a.arrows
            .iter()
            .flat_map(|h| super::leaders::head(*h))
            .any(|l| crosses(l, b.label_box))
        || (a.arrows.len() == 2
            && a.lines.len() >= 3
            && b.arrows.len() == 1
            && (!(allow_transverse)
                && super::leaders::meets_extensions(&a.lines[..2], &b.lines, b.label_box)
                || a.lines[..2].iter().any(|e| {
                    b.arrows
                        .iter()
                        .flat_map(|h| super::leaders::head(*h))
                        .any(|h| touches_witness(h, *e, 0.2))
                })
                || allow_transverse
                    && a.lines[..2]
                        .iter()
                        .any(|e| super::leaders::runs_beside(*e, &b.lines))))
        || (a.arrows.len() == 2 && b.arrows.len() == 2 && shared_dimension_line(&a.lines, &b.lines))
}

// Candidate witnesses do not change while testing their peers. Prepare
// their local axes and rendered heads once, retaining the original arithmetic.
struct WitnessFrame {
    from: P2,
    delta: P2,
    length: f64,
}
impl WitnessFrame {
    fn new(line: [P2; 2]) -> Self {
        let delta = [line[1][0] - line[0][0], line[1][1] - line[0][1]];
        Self {
            from: line[0],
            delta,
            length: delta[0].hypot(delta[1]),
        }
    }
    fn touches(&self, s: [P2; 2], pad: f64) -> bool {
        let (from, d, n) = (self.from, self.delta, self.length);
        if n < 1e-12 {
            return super::leaders::touches_box(
                s,
                [from[0] - pad, from[1] - pad, from[0] + pad, from[1] + pad],
            );
        }
        let local = s.map(|p| {
            let w = [p[0] - from[0], p[1] - from[1]];
            [
                (w[0] * d[0] + w[1] * d[1]) / n,
                (w[1] * d[0] - w[0] * d[1]) / n,
            ]
        });
        super::leaders::touches_box(local, [-pad, -pad, n + pad, pad])
    }
}
struct WitnessStroke<'a> {
    placed: &'a Placed,
    heads: [[[P2; 2]; 3]; 2],
    frames: std::cell::OnceCell<[WitnessFrame; 2]>,
}
impl<'a> WitnessStroke<'a> {
    fn new(placed: &'a Placed) -> Option<Self> {
        (placed.arrows.len() == 2 && placed.lines.len() >= 3).then(|| Self {
            placed,
            heads: [
                super::leaders::head(placed.arrows[0]),
                super::leaders::head(placed.arrows[1]),
            ],
            frames: std::cell::OnceCell::new(),
        })
    }
    fn contact(&self, peer: &WitnessPeer, transverse: bool) -> bool {
        let (a, b) = (self.placed, peer.placed);
        a.lines
            .iter()
            .take(2)
            .any(|line| crosses(*line, b.label_box))
            || self
                .heads
                .iter()
                .flatten()
                .any(|line| crosses(*line, b.label_box))
            || peer.head.as_ref().is_some_and(|head| {
                (!transverse
                    && super::leaders::meets_extensions(&a.lines[..2], &b.lines, b.label_box))
                    || self
                        .frames
                        .get_or_init(|| {
                            [WitnessFrame::new(a.lines[0]), WitnessFrame::new(a.lines[1])]
                        })
                        .iter()
                        .any(|frame| head.iter().any(|h| frame.touches(*h, 0.2)))
                    || (transverse
                        && a.lines[..2]
                            .iter()
                            .any(|e| super::leaders::runs_beside(*e, &b.lines)))
            })
            || (b.arrows.len() == 2 && shared_dimension_line(&a.lines, &b.lines))
    }
}
struct WitnessPeer<'a> {
    placed: &'a Placed,
    bounds: [f64; 4],
    head: Option<[[P2; 2]; 3]>,
}

// Broad phase for fixed witness contacts. Bounds include rendered peer heads
// and labels; query padding exceeds the largest head, corridor and shared-line
// tolerance. Every surviving pair still uses the complete original predicate.
struct WitnessPeers<'a>(Vec<WitnessPeer<'a>>);
impl<'a> WitnessPeers<'a> {
    fn new(peers: impl Iterator<Item = &'a Placed>) -> Self {
        Self(
            peers
                .map(|p| {
                    let b = ink_points(p).fold(
                        [
                            f64::INFINITY,
                            f64::INFINITY,
                            f64::NEG_INFINITY,
                            f64::NEG_INFINITY,
                        ],
                        |b, q| {
                            [
                                b[0].min(q[0]),
                                b[1].min(q[1]),
                                b[2].max(q[0]),
                                b[3].max(q[1]),
                            ]
                        },
                    );
                    WitnessPeer {
                        placed: p,
                        bounds: b,
                        head: (p.arrows.len() == 1).then(|| super::leaders::head(p.arrows[0])),
                    }
                })
                .collect(),
        )
    }
    fn contact(&self, p: &Placed, transverse: impl Fn(&Placed) -> bool) -> bool {
        let b = p.lines.iter().chain(&p.arrows).flatten().fold(
            [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ],
            |b, q| {
                [
                    b[0].min(q[0]),
                    b[1].min(q[1]),
                    b[2].max(q[0]),
                    b[3].max(q[1]),
                ]
            },
        );
        let pad = 2.0 + ARROW_HALF;
        let b = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
        let prepared = std::cell::OnceCell::new();
        self.0.iter().any(|peer| {
            boxes_overlap(b, peer.bounds)
                && prepared
                    .get_or_init(|| WitnessStroke::new(p))
                    .as_ref()
                    .map_or_else(
                        || linear_witness_contact(p, peer.placed, transverse(peer.placed)),
                        |a| a.contact(peer, transverse(peer.placed)),
                    )
        })
    }
}

// Text alternatives retain their witness strokes and heads. Once those
// constant contacts have been measured, query only the changing label/run-on.
struct PeerInk {
    labels: Vec<[f64; 4]>,
    lines: super::leaders::SegmentIndex,
}
impl PeerInk {
    fn new<'a>(peers: impl Iterator<Item = &'a Placed>) -> Self {
        let peers: Vec<_> = peers.collect();
        let labels = peers.iter().map(|q| q.label_box).collect();
        let lines = super::leaders::SegmentIndex::new(
            peers
                .iter()
                .flat_map(|q| {
                    q.lines
                        .iter()
                        .copied()
                        .chain(q.arrows.iter().flat_map(|a| super::leaders::head(*a)))
                })
                .collect(),
        );
        Self { labels, lines }
    }
    fn mutable_contact(&self, p: &Placed) -> bool {
        self.labels
            .iter()
            .any(|label| boxes_overlap(p.label_box, *label) || crosses(p.lines[2], *label))
            || self.lines.crosses_box(p.label_box)
    }
}

// Lane and witness alternatives change geometry, not printed text. Retain only
// the immutable standard-font bounds rather than outlining the same label for
// every candidate. The bounded cache is private to each planning thread.
fn linear_label_bounds(terms: &[String]) -> (P2, P2) {
    thread_local! {
        static BOUNDS: std::cell::RefCell<BTreeMap<Vec<String>, (P2, P2)>> = const { std::cell::RefCell::new(BTreeMap::new()) };
    }
    BOUNDS.with(|bounds| {
        if let Some(&value) = bounds.borrow().get(terms) {
            return value;
        }
        let ink = label_ink(terms, font());
        let value = (ink.min, ink.max);
        let mut bounds = bounds.borrow_mut();
        if bounds.len() >= 256 {
            bounds.clear();
        }
        bounds.insert(terms.to_vec(), value);
        value
    })
}

/// Actual axis pairs of a claimed grid pitch. Its two stations are fixed, but
/// either physical row/column may support the witnesses on an interior lane.
pub(super) fn pattern_references(w: &Want, analysis: &Analysis) -> Vec<Shape> {
    let [m] = w.measurements.as_slice() else {
        return Vec::new();
    };
    if !m.parameter.starts_with("grid_pitch.length.") {
        return Vec::new();
    }
    let Shape::Linear {
        a,
        b,
        axis,
        a_edge: false,
        b_edge: false,
        order,
    } = w.shape
    else {
        return Vec::new();
    };
    let Some(pattern) = analysis
        .recognition
        .as_ref()
        .and_then(|r| r.patterns.iter().find(|p| p.id == m.feature))
    else {
        return Vec::new();
    };
    let frame = crate::bridge::holes::frame(analysis);
    let points: Vec<_> = pattern
        .members
        .iter()
        .filter_map(|id| {
            let f = analysis.features.iter().find(|f| &f.id == id)?;
            crate::bridge::holes::Hole::read(f, &frame)
                .ok()
                .map(|h| h.at)
        })
        .collect();
    let mut pairs: Vec<(P3, P3)> = Vec::new();
    for p in points.iter().filter(|p| (p[axis] - a[axis]).abs() < 1e-6) {
        for q in points.iter().filter(|q| {
            (q[axis] - b[axis]).abs() < 1e-6
                && (0..3)
                    .filter(|k| *k != axis)
                    .all(|k| (p[k] - q[k]).abs() < 1e-6)
        }) {
            if !pairs.contains(&(*p, *q)) {
                pairs.push((*p, *q));
            }
        }
    }
    pairs.sort_by(|(a, _), (b, _)| {
        (0..3)
            .map(|k| a[k].total_cmp(&b[k]))
            .find(|o| !o.is_eq())
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    // The two boundary columns/rows suffice; an array cannot multiply retries.
    if pairs.len() > 2 {
        pairs = vec![pairs[0], pairs[pairs.len() - 1]];
    }
    pairs
        .into_iter()
        .map(|(a, b)| Shape::Linear {
            a,
            b,
            axis,
            order,
            a_edge: false,
            b_edge: false,
        })
        .collect()
}

/// A grid pitch may use either row of the same claimed pattern. Keep its exact
/// two measured stations and select a nearer pair of actual member axes. Both
/// starts must lie on the old strokes, so this only removes unnecessary ink.
pub(super) fn nearest_pattern_witnesses(
    analysis: &Analysis,
    frame: &Frame,
    measurements: &[super::Measurement],
    p: &mut Placed,
) {
    let Some(m) = measurements.first() else {
        return;
    };
    if !m.parameter.starts_with("grid_pitch.length.")
        || !measurements.iter().all(|x| x == m)
        || p.lines.len() < 3
        || p.arrows.len() != 2
    {
        return;
    }
    let Some(pattern) = analysis
        .recognition
        .as_ref()
        .and_then(|r| r.patterns.iter().find(|r| r.id == m.feature))
    else {
        return;
    };
    let qframe = crate::bridge::holes::frame(analysis);
    let points: Vec<_> = pattern
        .members
        .iter()
        .filter_map(|id| {
            let f = analysis.features.iter().find(|f| &f.id == id)?;
            crate::bridge::holes::Hole::read(f, &qframe)
                .ok()
                .map(|h| frame.page(h.at))
        })
        .collect();
    let normal = usize::from(p.rotation.abs() < 1e-9);
    let station = 1 - normal;
    let line = p.arrows[0][0][normal];
    let old = [p.lines[0][0], p.lines[1][0]];
    let sign = (p.lines[0][1][normal] - old[0][normal]).signum();
    let mut best = old;
    let mut length = (old[0][normal] - line).abs() + (old[1][normal] - line).abs();
    for a in points
        .iter()
        .filter(|a| (a[station] - old[0][station]).abs() < 1e-6)
    {
        for b in points.iter().filter(|b| {
            (b[station] - old[1][station]).abs() < 1e-6 && (a[normal] - b[normal]).abs() < 1e-6
        }) {
            let mut ends = [*a, *b];
            for e in &mut ends {
                e[normal] += sign * GAP;
            }
            if (0..2).any(|i| {
                ends[i][normal] < old[i][normal].min(line) - 1e-6
                    || ends[i][normal] > old[i][normal].max(line) + 1e-6
            }) {
                continue;
            }
            let candidate = (ends[0][normal] - line).abs() + (ends[1][normal] - line).abs();
            if candidate + 1e-6 < length {
                best = ends;
                length = candidate;
            }
        }
    }
    p.lines[0][0] = best[0];
    p.lines[1][0] = best[1];
}

pub(crate) fn nearest_shared_witnesses(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    views: &[crate::model::PlannedView],
    dimensions: &mut [super::PlannedDimension],
) {
    if !crate::nominal::geometry_eligible(analysis) {
        return;
    }
    let mut wants = None;
    for d in dimensions.iter_mut().filter(|d| d.kind == "linear") {
        if d.measurements.is_empty()
            || spec
                .dimensions
                .get(&d.id)
                .is_some_and(|s| s.place.is_some())
        {
            continue;
        }
        let first = &d.measurements[0];
        if !d
            .measurements
            .iter()
            .all(|m| m.parameter == first.parameter && (m.value - first.value).abs() < 1e-6)
        {
            continue;
        }
        let (Some(v), Some(p)) = (views.iter().find(|v| v.id == d.view), d.placed.as_mut()) else {
            continue;
        };
        if v.detail.is_some() || v.cut.is_some() || p.lines.len() < 3 || p.arrows.len() != 2 {
            continue;
        }
        let normal = if p.rotation.abs() < 1e-9 {
            1
        } else if (p.rotation - 90.).abs() < 1e-9 {
            0
        } else {
            continue;
        };
        let station = 1 - normal;
        if p.lines[..2]
            .iter()
            .any(|s| (s[0][station] - s[1][station]).abs() > 1e-9)
        {
            continue;
        }
        let frame = Frame::new(analysis, v);
        if automatic_slot_layout(analysis) {
            nearest_pattern_witnesses(analysis, &frame, &d.measurements, p);
        }
        let line = p.arrows[0][0][normal];
        let sign = (p.lines[0][1][normal] - p.lines[0][0][normal]).signum();
        // A straight row's constant coordinate belongs to every member, unlike
        // its along-row location, which must still locate the first member.
        if let Some(axis) = first
            .parameter
            .strip_prefix("location.length.")
            .and_then(|name| super::AXES.iter().position(|a| *a == name))
            && let Some(pattern) = analysis.recognition.as_ref().and_then(|r| {
                r.patterns
                    .iter()
                    .find(|row| row.id == first.feature && row.arrangement == "linear")
            })
        {
            let qframe = crate::bridge::holes::frame(analysis);
            let points: Option<Vec<_>> = pattern
                .members
                .iter()
                .map(|id| {
                    let feature = analysis.features.iter().find(|f| &f.id == id)?;
                    crate::bridge::holes::Hole::read(feature, &qframe)
                        .ok()
                        .map(|h| h.at)
                })
                .collect();
            if let Some(points) = points
                && points.len() > 1
                && points
                    .iter()
                    .all(|at| (at[axis] - points[0][axis]).abs() < 1e-6)
            {
                for at in points {
                    let at = frame.page(at);
                    for i in 0..2 {
                        if (at[station] - p.lines[i][0][station]).abs() > 1e-6 {
                            continue;
                        }
                        let start = p.lines[i][0][normal];
                        let candidate = at[normal] + sign * GAP;
                        if candidate >= start.min(line) - 1e-6
                            && candidate <= start.max(line) + 1e-6
                            && (candidate - line).abs() + 1e-6 < (start - line).abs()
                        {
                            p.lines[i][0][normal] = candidate;
                        }
                    }
                }
            }
        }
        if d.measurements.len() < 2 {
            continue;
        }
        let old = [p.lines[0][0], p.lines[1][0]];
        let mut best = old;
        let mut shortest = (old[0][normal] - line).abs() + (old[1][normal] - line).abs();
        for w in wants.get_or_insert_with(|| super::wants(analysis).0).iter() {
            if !w.measurements.iter().any(|m| d.measurements.contains(m)) {
                continue;
            }
            let Shape::Linear {
                a,
                b,
                a_edge: false,
                b_edge: false,
                ..
            } = w.shape
            else {
                continue;
            };
            let mut ends = [frame.page(a), frame.page(b)];
            if (ends[0][station] - old[1][station]).abs() < 1e-6
                && (ends[1][station] - old[0][station]).abs() < 1e-6
            {
                ends.swap(0, 1);
            }
            if (0..2).any(|i| (ends[i][station] - old[i][station]).abs() > 1e-6) {
                continue;
            }
            for end in &mut ends {
                end[normal] += sign * GAP;
            }
            // Each new origin lies on the existing stroke, with the same gap
            // from its own claimed model station. Removing ink cannot add a crossing.
            if (0..2).any(|i| {
                let x = ends[i][normal];
                x < old[i][normal].min(line) - 1e-6 || x > old[i][normal].max(line) + 1e-6
            }) {
                continue;
            }
            let length = (ends[0][normal] - line).abs() + (ends[1][normal] - line).abs();
            if length + 1e-6 < shortest {
                shortest = length;
                best = ends;
            }
        }
        p.lines[0][0] = best[0];
        p.lines[1][0] = best[1];
    }
}

fn place_linear_at(
    frame: &Frame,
    shape: &Shape,
    terms: &[String],
    side: Side,
    position: (f64, f64),
) -> Option<Placed> {
    let Shape::Linear {
        a,
        b,
        a_edge,
        b_edge,
        ..
    } = shape
    else {
        return None;
    };
    let horizontal = matches!(side, Side::Below | Side::Above);
    let (min, max) = linear_label_bounds(terms);
    let ink = LabelInk {
        min,
        max,
        glyphs: Vec::new(),
        strokes: Vec::new(),
    };
    let w = ink.max[0] - ink.min[0];
    let o = frame.outline;
    let (pa, pb) = (frame.page(*a), frame.page(*b));
    let (t_line, out) = position;
    // Work along the line (s) and across it (t), then map to the page.
    let k = usize::from(horizontal); // the page coordinate across the line
    let map = |s: f64, t: f64| if horizontal { [s, t] } else { [t, s] };
    let edge = match side {
        Side::Below => o[1],
        Side::Above => o[3],
        Side::Left => o[0],
        Side::Right => o[2],
    };
    let mut lines = Vec::new();
    let (s0, s1) = (pa[1 - k], pb[1 - k]);
    for (p, s, on_edge) in [(pa, s0, *a_edge), (pb, s1, *b_edge)] {
        let from = if on_edge { edge } else { p[k] };
        lines.push([map(s, from + out * GAP), map(s, t_line + out * OVERSHOOT)]);
    }
    let (lo, hi) = (s0.min(s1), s0.max(s1));
    let mut arrows = Vec::new();
    let mid = if hi - lo >= w + 2.0 * ARROW + 2.0 {
        lines.push([map(lo, t_line), map(hi, t_line)]);
        arrows.push(arrow(map(lo, t_line), map(lo + 1.0, t_line)));
        arrows.push(arrow(map(hi, t_line), map(hi - 1.0, t_line)));
        0.5 * (lo + hi) - 0.5 * w - ink.min[0]
    } else if hi-lo>=w+2.0 && frame.inspection.is_some_and(|(k,datum)| matches!(shape,Shape::Linear {a,b,axis,..} if *axis==k && ((a[k]-datum).abs()<1e-6 || (b[k]-datum).abs()<1e-6))) {
        // A baseline ordinate can keep its text within the measured span with outside arrows.
        // This avoids putting a short ordinate's text above the datum in the next view's band.
        lines.push([map(lo,t_line),map(hi,t_line)]);
        arrows.push(arrow(map(lo,t_line),map(lo-1.0,t_line)));
        arrows.push(arrow(map(hi,t_line),map(hi+1.0,t_line)));
        0.5*(lo+hi)-0.5*w-ink.min[0]
    } else {
        // Too short for the label between the arrows: the arrows point in from outside, and the
        // label sits beyond the far end.
        lines.push([map(lo - ARROW - 2.0, t_line), map(hi + ARROW + 2.0, t_line)]);
        arrows.push(arrow(map(lo, t_line), map(lo - 1.0, t_line)));
        arrows.push(arrow(map(hi, t_line), map(hi + 1.0, t_line)));
        hi + ARROW + 3.0 - ink.min[0]
    };
    // The label reads from below or from the right, above its line (ISO 129-1, aligned).
    let (label_at, rotation) = if horizontal {
        ([mid, t_line + LIFT], 0.0)
    } else {
        ([t_line - LIFT, mid], 90.0)
    };
    Some(Placed {
        lines,
        arrows,
        label_at,
        rotation,
        label_box: label_box(&ink, label_at, rotation),
    })
}

/// The step a label slides by along its dimension line, mm; the clearance it keeps from the
/// arrowheads at the line's ends; and how far past the line's end a label beyond it may go.
const SLIDE_STEP: f64 = 0.5;
const SLIDE_CLEAR: f64 = 0.5;
const SLIDE_REACH: f64 = 20.0;
/// Moves labels off other items' ink: a linear dimension whose label another item's line
/// crosses (an outer lane's extension line) or meets another label takes the nearest place clear
/// of both, along its own line: between its arrows it slides between them, right (or up) before
/// left; beyond the line's end (a span too short for it) it tries the other end, then further out
/// from either, the line running on to it, up to [`SLIDE_REACH`]. With no clear place an item
/// stays where it was, and the collision is reported. *items* are every linear item the part
/// would have, in plan order (callouts are placed after, around them, by [`super::leaders`]), so
/// where a label settles does not depend on what the spec omits (ARCH0003-2).
pub(super) fn settle(
    items: &mut [Option<Placed>],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
) {
    let page = Page::new(analysis, sheet, views);
    settle_where(
        items,
        &page,
        automatic_slot_layout(analysis),
        |_| true,
        |i, j| j != i,
    );
}

pub(super) fn settle_fixed(
    items: &mut [Option<Placed>],
    fixed: &[bool],
    obstacles: &[PlannedDimension],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
) {
    if fixed.iter().all(|f| !f) && obstacles.is_empty() {
        settle(items, analysis, sheet, views);
        return;
    }
    let n = items.len();
    let mut all = items.to_vec();
    all.extend(obstacles.iter().map(|d| d.placed.clone()));
    let page = Page::new(analysis, sheet, views);
    settle_where(
        &mut all,
        &page,
        automatic_slot_layout(analysis),
        |i| i < n && !fixed[i],
        |i, j| i != j,
    );
    items.clone_from_slice(&all[..n]);
}

#[allow(clippy::too_many_arguments)]
pub(super) fn avoid_fixed(
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    chosen: &[Option<&str>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
    fixed: &[bool],
    drawn: &[bool],
    placed: &mut [Option<Placed>],
    obstacles: &[PlannedDimension],
    page: &Page,
) {
    let held: Vec<(Placed, bool)> = placed
        .iter()
        .zip(fixed)
        .zip(items)
        .filter_map(|((p, f), w)| {
            if *f {
                Some((p.clone()?, matches!(w.shape, Shape::Callout { .. })))
            } else {
                None
            }
        })
        .chain(
            obstacles
                .iter()
                .filter_map(|d| Some((d.placed.clone()?, d.kind == "callout"))),
        )
        .collect();
    if held.is_empty() {
        return;
    }
    let blocked = |p: &Placed| {
        held.iter().any(|(q, leader)| {
            boxes_overlap(p.label_box, q.label_box)
                || p.lines.iter().any(|l| crosses(*l, q.label_box))
                || q.lines.iter().any(|l| crosses(*l, p.label_box))
                || (*leader
                    && q.lines.iter().any(|l| {
                        p.lines
                            .iter()
                            .chain(&p.arrows)
                            .any(|t| super::leaders::lines_meet(*l, *t))
                    }))
        })
    };
    for i in 0..items.len() {
        if fixed[i] {
            continue;
        }
        let (Some(p), Some(view), Some(side)) = (&placed[i], chosen[i], sides[i]) else {
            continue;
        };
        if !blocked(p) {
            continue;
        }
        let slid = slides(p, page.drawable).find(|p| page.fits(p, view) && !blocked(p));
        if let Some(p) = slid {
            placed[i] = Some(p);
            continue;
        }
        let original = p;
        let opposite = match side {
            Side::Above => Side::Below,
            Side::Below => Side::Above,
            Side::Left => Side::Right,
            Side::Right => Side::Left,
        };
        let candidates = (0..64)
            .map(|n| (side, n))
            .chain((0..64).map(|n| (opposite, n)));
        // Moving around any pin must preserve distinct peer dimension lines. A linear
        // pin obstructs the same lanes as a callout pin.
        for (side, lane) in candidates {
            if let Some(p) = place(&frames[view], &items[i], side, lane)
                && page.fits(&p, view)
                && !blocked(&p)
                && !placed.iter().enumerate().any(|(j, q)| {
                    drawn[i]
                        && drawn[j]
                        && j != i
                        && chosen[j] == Some(view)
                        && matches!(items[j].shape, Shape::Linear { .. })
                        && q.as_ref().is_some_and(|q| {
                            dimension_lines_overlap(&p, q) && !dimension_lines_overlap(original, q)
                        })
                })
            {
                placed[i] = Some(p);
                sides[i] = Some(side);
                lanes[i] = lane;
                break;
            }
        }
    }
}

/// The size and datum leg of the same slot are one semantic chain. Accidental
/// adjacency of unrelated spans does not freeze either dimension's lane.
pub(super) fn slot_chain_claims(a: &[super::Measurement], b: &[super::Measurement]) -> bool {
    a.iter().any(|a| {
        b.iter().any(|b| {
            a.feature == b.feature
                && ((a.parameter == "location_slot.length" && b.parameter == "slot_length.length")
                    || (b.parameter == "location_slot.length"
                        && a.parameter == "slot_length.length"))
        })
    })
}

/// End-to-end dimensions on one measured line form one corridor claim. Moving
/// only one member would break its datum chain even if its text still fits.
pub(super) fn forms_chain(a: &Placed, b: &Placed) -> bool {
    if a.arrows.len() != 2 || b.arrows.len() != 2 || a.rotation != b.rotation {
        return false;
    }
    let k = usize::from(a.rotation != 0.0);
    if (a.arrows[0][0][1 - k] - b.arrows[0][0][1 - k]).abs() > 1e-6 {
        return false;
    }
    let ends = |p: &Placed| {
        let (x, y) = (p.arrows[0][0][k], p.arrows[1][0][k]);
        (x.min(y), x.max(y))
    };
    let ((a0, a1), (b0, b1)) = (ends(a), ends(b));
    (a1 - b0).abs().min((b1 - a0).abs()) < 1e-6
}

/// A finite witness corridor, padded in its own axes. An oblique stroke's
/// axis-aligned bounding rectangle includes paper far away from its actual ink.
fn touches_witness(s: [P2; 2], witness: [P2; 2], pad: f64) -> bool {
    let d = [witness[1][0] - witness[0][0], witness[1][1] - witness[0][1]];
    let n = d[0].hypot(d[1]);
    if n < 1e-12 {
        return super::leaders::touches_box(
            s,
            [
                witness[0][0] - pad,
                witness[0][1] - pad,
                witness[0][0] + pad,
                witness[0][1] + pad,
            ],
        );
    }
    let local = s.map(|p| {
        let w = [p[0] - witness[0][0], p[1] - witness[0][1]];
        [
            (w[0] * d[0] + w[1] * d[1]) / n,
            (w[1] * d[0] - w[0] * d[1]) / n,
        ]
    });
    super::leaders::touches_box(local, [-pad, -pad, n + pad, pad])
}

/// A label that cannot slide clear of a witness may have a clear lane across its view.
/// Try that corridor before leaders settle around the edited linear ink. The caller
/// keeps canonical and authored placements; every candidate clears the full inventory.
#[allow(clippy::too_many_arguments)]
pub(super) fn clear_linear(
    analysis: &Analysis,
    cache: &super::footprints::Cache,
    views: &[PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    chosen: &[Option<&str>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
    placed: &mut [Option<Placed>],
    obstacles: &[PlannedDimension],
    locked: &[bool],
    page: &Page,
    yield_locations: bool,
) {
    // The same settled solve recurs in candidate and furniture retries. Every
    // mutable input and resolved frame belongs to this Analysis-scoped key.
    let geometry: Vec<_> = views
        .iter()
        .map(|v| (&v.id, v.direction, v.up, v.at, v.scale, v.cut.is_some()))
        .collect();
    let quantities: Vec<_> = items
        .iter()
        .map(|w| (&w.id, w.value, &w.shape, &w.measurements, &w.terms))
        .collect();
    let frame_geometry: Vec<_> = frames
        .iter()
        .map(|(name, f)| (name, f.memo_geometry()))
        .collect();
    let furniture: Vec<_> = obstacles
        .iter()
        .map(|d| (&d.id, &d.kind, &d.placed))
        .collect();
    let established_clear = crate::planning::lookup::<std::collections::BTreeSet<String>>(
        analysis,
        "established-clear-witnesses",
        &[],
    )
    .unwrap_or_default();
    let key = serde_json::to_vec(&(
        quantities,
        chosen,
        &*sides,
        &*lanes,
        &*placed,
        furniture,
        locked,
        page,
        geometry,
        frame_geometry,
        yield_locations,
        established_clear,
    ))
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("linear solve inputs serialize");
    let (next_sides, next_lanes, next_placed) =
        crate::planning::memo(analysis, "settled-linear-solve", key, || {
            let mut next_sides = sides.to_vec();
            let mut next_lanes = lanes.to_vec();
            let mut next_placed = placed.to_vec();
            clear_linear_uncached(
                analysis,
                cache,
                views,
                frames,
                items,
                chosen,
                &mut next_sides,
                &mut next_lanes,
                &mut next_placed,
                obstacles,
                locked,
                page,
                yield_locations,
            );
            // Two dimensions left on one dimension line is a hard finding. Where the solve
            // leaves any, solve once more with the new witnesses kept off the lines of the
            // locations yielded to them, and keep that where it shares fewer.
            let shared = shared_lines(&next_placed);
            if shared > 0 {
                let mut guarded = (sides.to_vec(), lanes.to_vec(), placed.to_vec());
                clear_linear_ordered(
                    analysis,
                    cache,
                    views,
                    frames,
                    items,
                    chosen,
                    &mut guarded.0,
                    &mut guarded.1,
                    &mut guarded.2,
                    obstacles,
                    locked,
                    page,
                    0,
                    yield_locations,
                    true,
                );
                if shared_lines(&guarded.2) < shared {
                    (next_sides, next_lanes, next_placed) = guarded;
                }
            }
            (next_sides, next_lanes, next_placed)
        });
    sides.copy_from_slice(&next_sides);
    lanes.copy_from_slice(&next_lanes);
    placed.clone_from_slice(&next_placed);
}

/// How many pairs of placed linear dimensions share one dimension line over overlapping spans.
fn shared_lines(placed: &[Option<Placed>]) -> usize {
    let linear: Vec<_> = placed
        .iter()
        .flatten()
        .filter(|p| p.arrows.len() == 2)
        .collect();
    linear
        .iter()
        .enumerate()
        .map(|(i, p)| {
            linear[i + 1..]
                .iter()
                .filter(|q| shared_dimension_line(&p.lines, &q.lines))
                .count()
        })
        .sum()
}

#[allow(clippy::too_many_arguments)]
fn clear_linear_uncached(
    analysis: &Analysis,
    cache: &super::footprints::Cache,
    views: &[PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    chosen: &[Option<&str>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
    placed: &mut [Option<Placed>],
    obstacles: &[PlannedDimension],
    locked: &[bool],
    page: &Page,
    yield_locations: bool,
) {
    let initial = (sides.to_vec(), lanes.to_vec(), placed.to_vec());
    clear_linear_ordered(
        analysis,
        cache,
        views,
        frames,
        items,
        chosen,
        sides,
        lanes,
        placed,
        obstacles,
        locked,
        page,
        0,
        yield_locations,
        false,
    );
    let recess = |w: &Want| {
        !w.measurements.is_empty()
            && w.measurements
                .iter()
                .all(|m| m.feature.starts_with("f:section_recess:"))
    };
    let count = items
        .iter()
        .filter(|w| recess(w) && matches!(w.shape, Shape::Linear { .. }))
        .count();
    if count < 2
        || !locked.iter().any(|p| *p)
        || !obstacles.iter().any(|d| d.id.starts_with("pmi:"))
    {
        return;
    }
    // Existing ink is held. Retry which new dimension claims its corridor first;
    // a greedy first location must not strand its more constrained neighbour.
    let rank = |positions: &[Option<Placed>]| {
        let other: Vec<_> = positions
            .iter()
            .enumerate()
            .filter_map(|(i, p)| Some((i, p.as_ref()?)))
            .collect();
        other
            .iter()
            .filter(|(i, _)| recess(&items[*i]))
            .map(|(i, p)| {
                usize::from(!page.fits(p, chosen[*i].unwrap_or_default()))
                    + other
                        .iter()
                        .filter(|(j, _)| j != i)
                        .map(|(_, q)| {
                            usize::from(boxes_overlap(p.label_box, q.label_box))
                                + usize::from(p.lines.iter().any(|l| crosses(*l, q.label_box)))
                                + usize::from(q.lines.iter().any(|l| crosses(*l, p.label_box)))
                                + usize::from(
                                    p.arrows.len() == 2
                                        && q.arrows.len() == 2
                                        && shared_dimension_line(&p.lines, &q.lines),
                                )
                        })
                        .sum::<usize>()
                    + obstacles
                        .iter()
                        .filter_map(|d| d.placed.as_ref())
                        .map(|q| {
                            usize::from(boxes_overlap(p.label_box, q.label_box))
                                + usize::from(p.lines.iter().any(|l| crosses(*l, q.label_box)))
                                + usize::from(q.lines.iter().any(|l| crosses(*l, p.label_box)))
                        })
                        .sum::<usize>()
            })
            .sum::<usize>()
    };
    let mut score = rank(placed);
    if score == 0 {
        return;
    }
    let mut best = (sides.to_vec(), lanes.to_vec(), placed.to_vec());
    for rotation in 1..count.min(6) {
        sides.copy_from_slice(&initial.0);
        lanes.copy_from_slice(&initial.1);
        placed.clone_from_slice(&initial.2);
        clear_linear_ordered(
            analysis,
            cache,
            views,
            frames,
            items,
            chosen,
            sides,
            lanes,
            placed,
            obstacles,
            locked,
            page,
            rotation,
            yield_locations,
            false,
        );
        let now = rank(placed);
        if now < score {
            score = now;
            best = (sides.to_vec(), lanes.to_vec(), placed.to_vec());
        }
        if score == 0 {
            break;
        }
    }
    sides.copy_from_slice(&best.0);
    lanes.copy_from_slice(&best.1);
    placed.clone_from_slice(&best.2);
}

#[allow(clippy::too_many_arguments)]
fn clear_linear_ordered(
    analysis: &Analysis,
    cache: &super::footprints::Cache,
    views: &[PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    chosen: &[Option<&str>],
    sides: &mut [Option<Side>],
    lanes: &mut [usize],
    placed: &mut [Option<Placed>],
    obstacles: &[PlannedDimension],
    locked: &[bool],
    page: &Page,
    rotation: usize,
    yield_locations: bool,
    guard_yielded: bool,
) {
    let held = locked.iter().any(|p| *p);
    let established_clear = crate::planning::lookup::<std::collections::BTreeSet<String>>(
        analysis,
        "established-clear-witnesses",
        &[],
    )
    .unwrap_or_default();
    // Semantic PMI leaders may cross a witness transversely. Their text,
    // arrowheads and parallel runs remain obstacles, and final adjudication
    // still reports each crossing. Ordinary free solves retain their complete
    // padded escape corridor; a held-ink retry can spend transverse crossings
    // on new information while retaining every text, head and parallel-run guard.
    let frames_ink: Vec<_> = obstacles
        .iter()
        .filter(|d| d.id.starts_with("pmi:") && d.kind == "callout")
        .filter_map(|d| d.placed.as_ref())
        .collect();
    let semantic_frame = |p: &Placed| frames_ink.iter().any(|q| std::ptr::eq(*q, p));
    let chained: Vec<_> = placed
        .iter()
        .enumerate()
        .map(|(i, p)| {
            p.as_ref().is_some_and(|p| {
                placed.iter().enumerate().any(|(j, q)| {
                    i != j
                        && chosen[i] == chosen[j]
                        && slot_chain_claims(&items[i].measurements, &items[j].measurements)
                        && q.as_ref().is_some_and(|q| forms_chain(p, q))
                })
            })
        })
        .collect();
    let mut models = BTreeMap::new();
    let recess = |i: usize| {
        !items[i].measurements.is_empty()
            && items[i]
                .measurements
                .iter()
                .all(|m| m.feature.starts_with("f:section_recess:"))
    };
    let recess_context = items.iter().any(|w| {
        w.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"))
    });
    let semantic_pmi = super::leaders::has_semantic_pmi(analysis, frames);
    let mut order: Vec<_> = (0..items.len()).collect();
    let movable = |i: usize| {
        recess(i)
            && matches!(items[i].shape, Shape::Linear { .. })
            && placed[i].is_some()
            && !locked.get(i).copied().unwrap_or(false)
    };
    order.sort_by_key(|&i| !movable(i));
    let end = order.iter().take_while(|&&i| movable(i)).count();
    if end > 0 {
        order[..end].rotate_left(rotation % end);
    }
    // Re-seat existing locations from the datum outward. A new recess witness
    // must not invert a settled location ladder while clearing its own corridor.
    let new_native = analysis
        .recognition
        .as_ref()
        .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "));
    let location = |i: usize| {
        new_native && recess_context && matches!(items[i].shape, Shape::Linear { order: 1, .. })
    };
    let old_location = |i: usize| !recess(i) && location(i);
    let slots: Vec<_> = order
        .iter()
        .enumerate()
        .filter(|(_, i)| old_location(**i))
        .map(|(n, _)| n)
        .collect();
    let mut locations: Vec<_> = slots.iter().map(|&n| order[n]).collect();
    locations.sort_by(|&a, &b| items[a].value.total_cmp(&items[b].value));
    for (slot, item) in slots.into_iter().zip(locations) {
        order[slot] = item;
    }
    let mut positions = vec![0; items.len()];
    for (n, &i) in order.iter().enumerate() {
        positions[i] = n;
    }
    // In the held-ink retry, later new items have not claimed a corridor yet.
    // Their failed template positions must not block the current item. Restore
    // each before its own solve, so no dimension disappears from the result.
    let mut pending = BTreeMap::new();
    if held && obstacles.iter().any(|d| d.id.starts_with("pmi:")) {
        for &i in order.iter().take(end) {
            if !chained[i]
                && let Some(p) = placed[i].take()
            {
                pending.insert(i, p);
            }
        }
    }
    // Reinsert outer locations in datum order after the new witnesses claim
    // their corridors. Otherwise an abandoned outer position can prevent the
    // entire ladder from moving together onto a valid neighbouring sheet.
    if yield_locations {
        for &i in order.iter().skip(end) {
            if old_location(i)
                && !chained[i]
                && !locked.get(i).copied().unwrap_or(false)
                && let Some(p) = placed[i].take()
            {
                pending.insert(i, p);
            }
        }
    }
    for i in order {
        let new_recess = recess(i);
        if let Some(p) = pending.remove(&i) {
            placed[i] = Some(p);
        }
        if !matches!(items[i].shape, Shape::Linear { .. })
            || chained[i]
            || locked.get(i).copied().unwrap_or(false)
        {
            continue;
        }
        let (Some(view), Some(side)) = (chosen[i], sides[i]) else {
            continue;
        };
        let location_order_conflict = |target: Side, c: &Placed, ink: &[Option<Placed>]| {
            // New recess witnesses choose their physical corridor first.
            // Repair the existing ladder around them, rather than constraining
            // new coordinates against one another before their routes settle.
            if !old_location(i) {
                return false;
            }
            let normal = usize::from(matches!(target, Side::Above | Side::Below));
            let outline = frames[view].outline();
            let centre = (outline[normal] + outline[normal + 2]) / 2.0;
            let distance = |q: &Placed| (q.arrows[0][0][normal] - centre).abs();
            ink.iter().enumerate().any(|(j, q)| {
                let nearer = (!semantic_pmi
                    && matches!(items[j].shape, Shape::Linear { order: 0, .. }))
                    || (location(j)
                        && positions[j] < positions[i]
                        && items[j].value < items[i].value - 1e-9);
                nearer
                    && chosen[j] == Some(view)
                    && sides[j] == Some(target)
                    && q.as_ref().is_some_and(|q| {
                        q.arrows.len() == 2
                            && q.rotation == c.rotation
                            && distance(c) < distance(q) + super::lane_pitch() - 1e-6
                    })
            })
        };
        let Some(p) = &placed[i] else {
            continue;
        };
        let conflict = |a: &Placed, b: &Placed| {
            linear_pair_contact(a, b, new_recess && (semantic_frame(b) || held))
        };
        let others: Vec<_> = placed
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .filter_map(|(_, p)| p.as_ref())
            .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
            .collect();
        // A settled witness can separate a short peer's text from its own
        // arrow even without touching a glyph. Repair that witness as well
        // as trying to move the already detached label.
        let separated_labels: Vec<_> = placed
            .iter()
            .enumerate()
            .filter(|(j, _)| {
                crate::nominal::geometry_eligible(analysis)
                    && !crate::nominal::comparison()
                    && *j != i
                    && chosen[*j] == Some(view)
            })
            .filter(|(j, _)| {
                matches!(items[*j].shape, Shape::Linear { order: 0, .. })
                    && items[*j]
                        .measurements
                        .iter()
                        .any(|m| !m.feature.starts_with("f:section_recess:"))
            })
            .filter_map(|(_, q)| q.as_ref())
            .filter(|q| {
                !super::label_lanes::off_span(q, std::iter::empty())
                    && super::label_lanes::off_span(q, std::iter::once(p))
            })
            .collect();
        let label_conflict = !separated_labels.is_empty()
            || location_order_conflict(side, p, placed)
            || !page.fits(p, view)
            || others.iter().any(|q| conflict(p, q))
            || (recess_context && super::label_lanes::off_span(p, others.iter().copied()));
        let Some(v) = views.iter().find(|v| v.id.strip_prefix("v:") == Some(view)) else {
            continue;
        };
        let footprint = cache.get(analysis, v);
        // The Analysis projects a conservative superset of visible edges, including
        // cylinder silhouettes. Own-view ink is not a Page::fits obstacle: witnesses
        // normally start there, but must not traverse the body to reach another strip.
        let model = models
            .entry(view)
            .or_insert_with(|| cache.model(analysis, v));
        let extension_counter = footprint.extension_counter(&items[i].measurements);
        let measured_features = std::cell::RefCell::new(BTreeMap::new());
        let measured_geometry = std::cell::RefCell::new(BTreeMap::new());
        let feature_crossings = |c: &Placed| {
            c.lines
                .iter()
                .take(2)
                .map(|line| {
                    let key = line.map(|p| p.map(f64::to_bits));
                    if let Some(hits) = measured_features.borrow().get(&key) {
                        return *hits;
                    }
                    let hits = extension_counter.count(*line);
                    measured_features.borrow_mut().insert(key, hits);
                    hits
                })
                .sum::<usize>()
        };
        // Recess drawings already have a guarded joint corridor solve. The
        // new ordinary-witness retry must not bypass that established policy.
        let feature_conflict =
            physical_witness_retry(analysis) && !recess_context && feature_crossings(p) > 0;
        let crossings = |c: &Placed| {
            let features = feature_crossings(c);
            let geometry = c
                .lines
                .iter()
                .take(2)
                .map(|line| {
                    let key = line.map(|p| p.map(f64::to_bits));
                    if let Some(hits) = measured_geometry.borrow().get(&key) {
                        return *hits;
                    }
                    let hits = model.as_ref().map_or(0, |projection| {
                        let (edges, boxes) = projection.as_ref();
                        edges.crossings(*line)
                            + boxes.iter().filter(|b| crosses(*line, **b)).count()
                    });
                    measured_geometry.borrow_mut().insert(key, hits);
                    hits
                })
                .sum::<usize>();
            if recess_context || feature_conflict {
                (features, geometry)
            } else {
                (0, features + geometry)
            }
        };
        let before = crossings(p);

        // Expanded native inventories can displace an established location's
        // witness onto another hole even when neither is a recess. Give that
        // physical crossing the same bounded corridor repair as new witnesses
        // when the established drawing proves it was clear before expansion.
        let recess_corridor = (before.0 > 0
            && ((old_location(i) && established_clear.contains(&items[i].id))
                || (new_native
                    && crate::nominal::geometry_eligible(analysis)
                    && !crate::nominal::comparison()
                    && matches!(items[i].shape, Shape::Linear { order: 0, .. }))))
            || items[i]
                .measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
            || p.lines.iter().take(2).any(|line| {
                footprint
                    .crossed_extension(*line, &items[i].measurements)
                    .iter()
                    .any(|id| id.starts_with("f:section_recess:"))
            });
        if !feature_conflict
            && ((recess_context && !recess_corridor && !label_conflict)
                || (!label_conflict && (!recess_corridor || before == (0, 0))))
        {
            // A settled location's witness may still reach its station across
            // another feature inside the body. Without a corridor repair, begin it
            // beyond the near face on the same lane, as the repair itself would:
            // this only removes ink, so no other item can gain a contact.
            if recess_context
                && !semantic_pmi
                && new_native
                && old_location(i)
                && before.0 > 0
                && let Some(near_face) =
                    near_face_start(&LinearStroke::new(p), frames[view].outline(), side)
            {
                let mut shortened = p.clone();
                near_face.write(&mut shortened);
                if crossings(&shortened).0 < before.0 {
                    placed[i] = Some(shortened);
                }
            }
            continue;
        }
        // Prefer the least obstructed of the opposite strip's first eight lanes.
        // Keeping a reported label conflict is better than adding body crossings.
        let paper = page.drawable();
        let max_lane = (((paper[2] - paper[0]).max(paper[3] - paper[1]) / super::lane_pitch())
            .ceil() as usize)
            .max(16);
        let choices: Vec<_> = if recess_context
            || feature_conflict
            || (semantic_pmi && physical_witness_retry(analysis))
        {
            [side, opposite(side)]
                .into_iter()
                .flat_map(|side| {
                    (0..if recess_context { max_lane } else { 8 }).map(move |lane| (side, lane))
                })
                .collect()
        } else {
            (0..8).map(|lane| (opposite(side), lane)).collect()
        };
        let repair_witnesses: Vec<_> = placed
            .iter()
            .map(|q| q.as_ref().is_some_and(|q| page.witnesses_fit(q, view)))
            .collect();
        let immovable: Vec<_> = obstacles
            .iter()
            .filter_map(|d| d.placed.as_ref())
            .chain(
                placed
                    .iter()
                    .enumerate()
                    .filter(|(j, _)| {
                        *j != i
                            && (sides[*j].is_none()
                                || locked.get(*j).copied().unwrap_or(false)
                                || chosen[*j] != Some(view)
                                || !repair_witnesses[*j]
                                || placed[*j].as_ref().is_some_and(|q| q.arrows.len() != 2))
                    })
                    .filter_map(|(_, p)| p.as_ref()),
            )
            .collect();
        // Sliding this label cannot repair its witness or head crossing fixed
        // text, nor a fixed leader's forbidden contact with those witnesses.
        let transverse_allowed = |b: &Placed| new_recess && (semantic_frame(b) || held);
        let fixed_witnesses = WitnessPeers::new(immovable.iter().copied());
        let all_witnesses = WitnessPeers::new(others.iter().copied());
        let all_peer_ink = PeerInk::new(others.iter().copied());
        let fixed_peer_ink = PeerInk::new(immovable.iter().copied());
        let peer_witnesses = super::leaders::SegmentIndex::new(
            placed
                .iter()
                .enumerate()
                .filter(|(j, _)| *j != i)
                .filter_map(|(_, q)| q.as_ref())
                .flat_map(|q| q.lines.iter().take(2).copied())
                .collect(),
        );
        // Most trials never need to move a peer label. Prepare a peer's full
        // immutable obstacle list only if that particular repair is reached,
        // instead of building every quadratic list for every current dimension.
        let repair_peers: Vec<_> = (0..placed.len())
            .map(|_| std::cell::OnceCell::new())
            .collect();
        let repair_static: Vec<_> = (0..placed.len())
            .map(|_| std::cell::OnceCell::new())
            .collect();
        let repair_clearance = std::cell::RefCell::new(std::collections::HashMap::new());
        let best_rank = std::cell::Cell::new(None);
        let physical_trial = std::cell::RefCell::new(Placed {
            lines: vec![[[0.; 2]; 2]; 3],
            arrows: vec![[[0.; 2]; 2]; 2],
            label_at: [0.; 2],
            rotation: 0.,
            label_box: [0.; 4],
        });
        // A settled location yielded to the new witnesses comes back where it was, and
        // its own repair may find no other lane clear (FTC-08's 49.124 and 64.999, the
        // slot location held by the slot's callout's leader). In the guarded re-solve
        // ([`clear_linear`]) a new witness first looks for a lane without a dimension line
        // over such a location's span; only when none is clear are those lanes tried.
        let yield_guard = std::cell::Cell::new(guard_yielded);
        let yield_guarded = std::cell::Cell::new(false);
        let yielded_line = |c: &Placed| {
            let shared = yield_guard.get()
                && pending
                    .values()
                    .any(|q| q.arrows.len() == 2 && shared_dimension_line(&c.lines, &q.lines));
            if shared {
                yield_guarded.set(true);
            }
            shared
        };
        let mut found = None;
        // Exhaust perpendicular lanes on both sides, including finite start gaps,
        // before considering any oblique witness. Crossing minimisation alone must
        // never trade a readable ordinary location for a diagonal through the body.
        for (pass, oblique) in [(0, false), (0, true), (1, false), (1, true)] {
            if pass == 1 {
                if !yield_guarded.get() {
                    break;
                }
                yield_guard.set(false);
            }
            best_rank.set(None);
            let candidates = choices
                .iter()
                .copied()
                .flat_map(|(target, lane)| {
                    // Crossing counts and peer repairs cannot be negative. Skip
                    // preparing strokes whose best possible score already loses.
                    if best_rank
                        .get()
                        .is_some_and(|best| best <= ((0, 0), 0, target != side, lane))
                    {
                        return Vec::new();
                    }
                    let Some(mut c) = place(&frames[view], &items[i], target, lane) else {
                        return Vec::new();
                    };
                    if feature_conflict {
                        nearest_pattern_witnesses(
                            analysis,
                            &frames[view],
                            &items[i].measurements,
                            &mut c,
                        );
                    }
                    let c = LinearStroke::new(&c);
                    let mut strokes = vec![c];
                    if recess_context
                        && recess_corridor
                        && items[i]
                            .measurements
                            .iter()
                            .any(|m| m.parameter.contains("location"))
                    {
                        // Keep a location witness's gap clear of a leader attached
                        // near the same small opening. Its measured ends do not move.
                        let normal = usize::from(c.rotation == 0.0);
                        let out = if matches!(target, Side::Below | Side::Left) {
                            -1.0
                        } else {
                            1.0
                        };
                        for extra in [0.25, 0.5, 1.0] {
                            let mut spaced = c;
                            if spaced
                                .lines
                                .iter()
                                .take(2)
                                .any(|l| (l[1][normal] - l[0][normal]).abs() <= extra + 1.0)
                            {
                                continue;
                            }
                            for line in spaced.lines.iter_mut().take(2) {
                                line[0][normal] += out * extra;
                            }
                            strokes.push(spaced);
                        }
                        // A hole location starts at its axis, whereas the bore
                        // leader starts on its opening rim. On the opposite strip,
                        // start the free witness two millimetres beyond that rim,
                        // rather than through its own leader's attachment corridor.
                        // The datum witness keeps its original gap.
                        if let Shape::Linear { a_edge, b_edge, .. } = items[i].shape
                            && a_edge != b_edge
                        {
                            let rim = items[i]
                                .measurements
                                .iter()
                                .filter_map(|m| {
                                    let f = analysis
                                        .features
                                        .iter()
                                        .find(|f| f.id == m.feature && f.kind == "hole")?;
                                    Some(f.parameters.get("diameter")?.as_f64()? * v.scale / 2.0)
                                })
                                .fold(0.0_f64, f64::max);
                            let end = usize::from(a_edge);
                            let extra = rim + 1.0;
                            if rim > 0.0
                                && (c.lines[end][1][normal] - c.lines[end][0][normal]).abs()
                                    > extra + 1.0
                            {
                                let mut spaced = c;
                                spaced.lines[end][0][normal] += out * extra;
                                strokes.push(spaced);
                            }
                        }
                    }
                    if recess_context && !semantic_pmi && new_native {
                        // Keep the projected coordinate, but begin its finite
                        // witness beyond the near side of the projected body when
                        // its original support would traverse a recess. This is the
                        // same perpendicular coordinate with a longer start gap;
                        // no measured station or arrow is shifted sideways.
                        if let Some(near_face) = near_face_start(&c, frames[view].outline(), target)
                        {
                            strokes.push(near_face);
                        }
                    }
                    if recess_context
                        && !semantic_pmi
                        && super::has_new_native_recess_dimensions(analysis)
                    {
                        // A perpendicular witness can keep its measurement station
                        // while starting beyond a settled callout's finite corridor.
                        // Use the same label/leader padding as the callout router;
                        // otherwise a near-miss here evicts that leader later.
                        let normal = usize::from(c.rotation == 0.0);
                        let out = if matches!(target, Side::Below | Side::Left) {
                            -1.0
                        } else {
                            1.0
                        };
                        let bases = strokes.clone();
                        for mut clear in bases {
                            for line in clear.lines.iter_mut().take(2) {
                                for peer in others
                                    .iter()
                                    .filter(|p| p.arrows.len() == 1 && p.lines.len() == 2)
                                {
                                    let b = peer.label_box;
                                    let boxes = std::iter::once([
                                        b[0] - 1.0,
                                        b[1] - 1.0,
                                        b[2] + 1.0,
                                        b[3] + 1.0,
                                    ])
                                    .chain(
                                        peer.lines.iter().map(|[a, b]| {
                                            [
                                                a[0].min(b[0]) - 1.0,
                                                a[1].min(b[1]) - 1.0,
                                                a[0].max(b[0]) + 1.0,
                                                a[1].max(b[1]) + 1.0,
                                            ]
                                        }),
                                    );
                                    for b in boxes {
                                        if super::leaders::touches_box(*line, b) {
                                            let from = b[normal + if out > 0.0 { 2 } else { 0 }]
                                                + out * 1e-6;
                                            if out * (from - line[0][normal]) > 0.0 {
                                                line[0][normal] = from;
                                            }
                                        }
                                    }
                                }
                            }
                            if clear
                                .lines
                                .iter()
                                .take(2)
                                .all(|l| out * (l[1][normal] - l[0][normal]) > 1.0)
                            {
                                strokes.push(clear);
                            }
                        }
                    }
                    if new_recess
                        && items[i]
                            .measurements
                            .iter()
                            .all(|m| m.parameter == "channel_width.length")
                    {
                        for peer in items.iter().filter(|peer| {
                            peer.id != items[i].id
                                && peer
                                    .measurements
                                    .iter()
                                    .any(|m| items[i].measurements.contains(m))
                        }) {
                            let mut alternative = items[i].clone();
                            alternative.shape = peer.shape.clone();
                            if let Some(p) = place(&frames[view], &alternative, target, lane) {
                                strokes.push(LinearStroke::new(&p));
                            }
                        }
                    }
                    if recess_context
                        && new_native
                        && before.0 > 0
                        && items[i].measurements.iter().any(|m| {
                            m.feature == "step_level"
                                && m.parameter.starts_with("step_height.length")
                        })
                        && let Shape::Linear {
                            a,
                            b,
                            axis,
                            a_edge,
                            b_edge,
                            order,
                        } = items[i].shape
                        && axis == 2
                        && let Some(support) = analysis
                            .level_supports
                            .iter()
                            .rev()
                            .find(|s| (s.z - b[2]).abs() < 1e-3)
                    {
                        // A height witnesses the same recorded face at either end
                        // of its support. Use its other real edge when a newly read
                        // recess occupies the old, right-hand witness corridor.
                        let mut alternate = items[i].clone();
                        let mut from = a;
                        let mut to = b;
                        from[0] = support.x_span[0];
                        to[0] = support.x_span[0];
                        alternate.shape = Shape::Linear {
                            a: from,
                            b: to,
                            axis,
                            a_edge,
                            b_edge,
                            order,
                        };
                        if let Some(p) = place(&frames[view], &alternate, target, lane) {
                            strokes.push(LinearStroke::new(&p));
                        }
                    }
                    if oblique {
                        if !recess_context {
                            return Vec::new();
                        }
                        let bases = std::mem::take(&mut strokes);
                        for base in bases {
                            let k = usize::from(base.rotation != 0.0);
                            let normal = 1 - k;
                            // The same 30-degree inclination from the perpendicular for
                            // both witnesses; origins retain their measured stations.
                            if (base.lines[0][0][normal] - base.lines[1][0][normal]).abs() > 1e-6 {
                                continue;
                            }
                            for slope in [1.0 / 3.0_f64.sqrt(), -1.0 / 3.0_f64.sqrt()] {
                                let mut skew = base;
                                let dimension = base.arrows[0][0][normal];
                                let shift = slope * (dimension - base.lines[0][0][normal]);
                                for l in skew.lines.iter_mut().take(2) {
                                    l[1][k] += slope * (l[1][normal] - l[0][normal]);
                                }
                                for q in skew
                                    .lines
                                    .iter_mut()
                                    .skip(2)
                                    .chain(&mut skew.arrows)
                                    .flatten()
                                {
                                    q[k] += shift;
                                }
                                skew.label_at[k] += shift;
                                skew.label_box[k] += shift;
                                skew.label_box[k + 2] += shift;
                                // Compare with this lane's perpendicular option, not a
                                // blocked incumbent on a different side of the part.
                                let mut base_placed = p.clone();
                                base.write(&mut base_placed);
                                let mut skew_placed = p.clone();
                                skew.write(&mut skew_placed);
                                let material =
                                    crate::material::solids(analysis, |p| frames[view].page(p));
                                let traversal = |c: &Placed| {
                                    c.lines
                                        .iter()
                                        .take(2)
                                        .map(|l| {
                                            crate::material::intervals(&material, *l)
                                                .iter()
                                                .map(|(a, b)| b - a)
                                                .sum::<f64>()
                                        })
                                        .sum::<f64>()
                                };
                                let skew_hits = crossings(&skew_placed);
                                let base_hits = crossings(&base_placed);
                                if skew_hits.0 > base_hits.0
                                    || skew_hits.1 > base_hits.1
                                    || traversal(&skew_placed) > traversal(&base_placed) + 1e-6
                                    || traversal(&skew_placed) > crate::material::FLOOR
                                    || others.iter().any(|q| {
                                        skew_placed.lines.iter().take(2).any(|l| {
                                            crosses(*l, q.label_box)
                                                || q.lines
                                                    .iter()
                                                    .copied()
                                                    .chain(
                                                        q.arrows
                                                            .iter()
                                                            .flat_map(|a| super::leaders::head(*a)),
                                                    )
                                                    .any(|t| super::leaders::lines_meet(*l, t))
                                        })
                                    })
                                {
                                    continue;
                                }
                                strokes.push(skew);
                            }
                        }
                    }
                    strokes
                        .into_iter()
                        .map(|c| (target, lane, c))
                        .collect::<Vec<_>>()
                })
                .flat_map(|(target, lane, stroke)| {
                    let mut trial = physical_trial.borrow_mut();
                    stroke.write(&mut trial);
                    let stroke = &*trial;
                    // Visit strokes in exactly the original order, but do not prepare
                    // every later stroke's labels before the first one can win.
                    // Label slides cannot repair an invalid physical witness.
                    if !page.witnesses_fit(stroke, view)
                        || fixed_witnesses.contact(stroke, transverse_allowed)
                        || yielded_line(stroke)
                    {
                        return Vec::new();
                    }
                    // Feature crossings precede dense model-edge crossings in
                    // the unchanged rank. With nonnegative remaining counts, an
                    // already losing feature tier needs no model measurement.
                    if recess_context || feature_conflict {
                        let bound = (feature_crossings(stroke), 0);
                        // Ordinary crossing retries must remove a foreign-feature
                        // contact. Fewer dense model edges alone must not replan a
                        // clear label while retaining the very same obstruction.
                        if feature_conflict && !label_conflict && bound.0 >= before.0 {
                            return Vec::new();
                        }

                        if (bound > before && !(new_recess && label_conflict))
                            || (!label_conflict && bound >= before)
                            || best_rank
                                .get()
                                .is_some_and(|best| best <= (bound, 0, target != side, lane))
                        {
                            return Vec::new();
                        }
                    }
                    let hits = crossings(stroke);

                    if (hits > before && !(new_recess && label_conflict))
                        || (!label_conflict && hits >= before)
                    {
                        return Vec::new();
                    }
                    // Text moves cannot change the two witness crossing tiers. Even
                    // with zero peer repairs this stroke cannot improve the first
                    // accepted candidate, so retain the original tie order cheaply.
                    let lower_bound = (hits, 0, target != side, lane);
                    if best_rank.get().is_some_and(|best| best <= lower_bound) {
                        return Vec::new();
                    }
                    let constant_contact = all_witnesses.contact(stroke, transverse_allowed);
                    let mut choices = vec![stroke.clone()];
                    let needs_text = !page.fits(stroke, view)
                        || constant_contact
                        || all_peer_ink.mutable_contact(stroke)
                        || super::label_lanes::off_span(stroke, others.iter().copied())
                        || others.iter().any(|q| {
                            super::label_lanes::off_span(q, std::iter::once(stroke))
                                && !super::label_lanes::off_span(q, std::iter::once(p))
                        });
                    if (recess_context || feature_conflict) && needs_text {
                        choices.extend(super::label_lanes::centered_short(stroke));
                        choices.extend(
                            super::label_lanes::alternatives_placed(
                                stroke,
                                page.drawable(),
                                &others,
                            )
                            .take(8),
                        );
                    }
                    choices
                        .into_iter()
                        .map(|c| (target, lane, c, constant_contact))
                        .collect::<Vec<_>>()
                })
                .filter_map(|(target, lane, c, constant_contact)| {
                    if location_order_conflict(target, &c, placed) || !page.fits(&c, view) {
                        return None;
                    }
                    let hits = crossings(&c);
                    if (hits > before && !(new_recess && label_conflict))
                        || (!label_conflict && hits >= before)
                    {
                        return None;
                    }
                    if fixed_peer_ink.mutable_contact(&c) || peer_witnesses.crosses_box(c.label_box)
                    {
                        return None;
                    }
                    let mut repairs: Vec<(usize, Placed)> = Vec::new();
                    if recess_context || feature_conflict {
                        // A new witness may pass between a short peer's arrow and its
                        // text. Re-seat that text at its other arrow in the same solve;
                        // the measured span and witness lane remain fixed.
                        for (j, q) in placed
                            .iter()
                            .enumerate()
                            .filter_map(|(j, q)| Some((j, q.as_ref()?)))
                        {
                            if j == i {
                                continue;
                            }
                            let new_span = super::label_lanes::off_span(q, std::iter::once(&c))
                                && !super::label_lanes::off_span(q, std::iter::once(p));
                            let text_crossed = c.lines.iter().any(|l| crosses(*l, q.label_box));
                            if !new_span && !text_crossed {
                                continue;
                            }
                            if locked.get(j).copied().unwrap_or(false) || repairs.len() >= 8 {
                                return None;
                            }
                            if chosen[j] != Some(view) || sides[j].is_none() || q.arrows.len() != 2
                            {
                                continue;
                            }
                            if !repair_witnesses[j] {
                                return None;
                            }
                            let peers = repair_peers[j].get_or_init(|| {
                                placed
                                    .iter()
                                    .enumerate()
                                    .filter(|(t, _)| *t != i && *t != j)
                                    .filter_map(|(_, q)| q.as_ref())
                                    .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
                                    .collect::<Vec<_>>()
                            });
                            let rest: Vec<_> =
                                peers.iter().copied().chain(std::iter::once(&c)).collect();
                            let repaired =
                                super::label_lanes::alternatives_placed(q, page.drawable(), &rest)
                                    .find(|r| {
                                        if !page.fits(r, view)
                                            || conflict(r, &c)
                                            || super::label_lanes::off_span(r, std::iter::once(&c))
                                        {
                                            return false;
                                        }

                                        // Only the current witness varies during this search.
                                        // A peer label's clearance against the unchanged drawing
                                        // is identical at each repeated label/line position.
                                        let bits: [u64; 8] = r
                                            .label_box
                                            .iter()
                                            .copied()
                                            .chain(r.lines[2].into_iter().flatten())
                                            .map(f64::to_bits)
                                            .collect::<Vec<_>>()
                                            .try_into()
                                            // Invariant: Repair peers were filtered to placed linear dimensions.
                                            .expect("repair geometry");
                                        let key = (j, bits);
                                        let cached = repair_clearance.borrow().get(&key).copied();
                                        cached.unwrap_or_else(|| {
                                            let (labels, lines) =
                                                repair_static[j].get_or_init(|| {
                                                    // Original contacts are allowed. A pure text slide
                                                    // changes only the label and line run-on against
                                                    // every remaining fixed peer.
                                                    let constraints: Vec<_> = peers
                                                        .iter()
                                                        .copied()
                                                        .filter(|t| !conflict(q, t))
                                                        .collect();
                                                    let labels: Vec<_> = constraints
                                                        .iter()
                                                        .map(|t| t.label_box)
                                                        .collect();
                                                    let lines = super::leaders::SegmentIndex::new(
                                                        constraints
                                                            .iter()
                                                            .flat_map(|t| {
                                                                t.lines.iter().copied().chain(
                                                                    t.arrows.iter().flat_map(|a| {
                                                                        super::leaders::head(*a)
                                                                    }),
                                                                )
                                                            })
                                                            .collect(),
                                                    );
                                                    (labels, lines)
                                                });
                                            let clear = !super::label_lanes::off_span(
                                                r,
                                                peers.iter().copied(),
                                            ) && labels.iter().all(|label| {
                                                !boxes_overlap(r.label_box, *label)
                                                    && !crosses(r.lines[2], *label)
                                            }) && !lines.crosses_box(r.label_box);
                                            let mut saved = repair_clearance.borrow_mut();
                                            if saved.len() >= 4096 {
                                                saved.clear();
                                            }
                                            saved.insert(key, clear);
                                            clear
                                        })
                                    });
                            if let Some(r) = repaired {
                                repairs.push((j, r));
                            }
                        }
                    }
                    let final_peers: Vec<_> = placed
                        .iter()
                        .enumerate()
                        .filter(|(j, _)| *j != i)
                        .filter_map(|(j, q)| {
                            repairs
                                .iter()
                                .find(|(t, _)| *t == j)
                                .map(|(_, r)| r)
                                .or(q.as_ref())
                        })
                        .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
                        .collect();
                    let pair_contact = if repairs.is_empty() {
                        constant_contact || all_peer_ink.mutable_contact(&c)
                    } else {
                        final_peers.iter().any(|q| conflict(&c, q))
                    };
                    if pair_contact
                        || separated_labels
                            .iter()
                            .any(|q| super::label_lanes::off_span(q, std::iter::once(&c)))
                        || (recess_context
                            && (super::label_lanes::off_span(&c, final_peers.iter().copied())
                                || final_peers.iter().any(|q| {
                                    super::label_lanes::off_span(q, std::iter::once(&c))
                                        && !super::label_lanes::off_span(q, std::iter::once(p))
                                })))
                    {
                        return None;
                    }
                    for (j, r) in &repairs {
                        // Invariant: The repair list contains only placed peers.
                        let old = placed[*j].as_ref().unwrap();
                        if final_peers
                            .iter()
                            .any(|q| !std::ptr::eq(*q, r) && conflict(r, q) && !conflict(old, q))
                        {
                            return None;
                        }
                    }
                    Some((hits, target, lane, c, repairs))
                });
            for candidate in candidates {
                let score = (
                    candidate.0,
                    candidate.4.len(),
                    candidate.1 != side,
                    candidate.2,
                );
                let optimal = candidate.0 == (0, 0) && candidate.4.is_empty();
                if best_rank.get().is_none_or(|best| score < best) {
                    best_rank.set(Some(score));
                    found = Some(candidate);
                }
                // Current-strip lanes precede opposite-strip lanes, each increasing.
                // Once both crossing tiers and repairs are zero, every later lane
                // loses or ties the original lexicographic rank on either strip.
                if optimal {
                    break;
                }
            }

            if found.is_some() {
                break;
            }
        }

        if let Some((_, target, lane, c, repairs)) = found {
            sides[i] = Some(target);
            lanes[i] = lane;
            placed[i] = Some(c);
            for (j, r) in repairs {
                placed[j] = Some(r);
            }
        }
    }
}

/// The same witnesses, begun beyond the near side of the projected body toward *target*;
/// `None` when a witness would then be too short. No station or arrow moves.
fn near_face_start(c: &LinearStroke, outline: [f64; 4], target: Side) -> Option<LinearStroke> {
    let normal = usize::from(c.rotation == 0.0);
    let out = if matches!(target, Side::Below | Side::Left) {
        -1.0
    } else {
        1.0
    };
    let face = outline[normal + if out > 0.0 { 2 } else { 0 }];
    let mut near_face = *c;
    for line in near_face.lines.iter_mut().take(2) {
        let from = face + out * GAP;
        if out * (from - line[0][normal]) > 0.0 {
            line[0][normal] = from;
        }
    }
    near_face
        .lines
        .iter()
        .take(2)
        .all(|l| out * (l[1][normal] - l[0][normal]) > 1.0)
        .then_some(near_face)
}

// A linear trial has exactly two witnesses, one dimension line and two arrows.
// Keep rejected trials inline; allocate public Placed vectors only for the
// survivors that need text alternatives or can become the selected result.
#[derive(Clone, Copy)]
struct LinearStroke {
    lines: [[P2; 2]; 3],
    arrows: [[P2; 2]; 2],
    label_at: P2,
    rotation: f64,
    label_box: [f64; 4],
}
impl LinearStroke {
    fn new(p: &Placed) -> Self {
        Self {
            // Invariant: Linear placement constructs exactly three strokes.
            lines: p.lines.as_slice().try_into().expect("three linear strokes"),
            // Invariant: Linear placement constructs exactly two arrows.
            arrows: p.arrows.as_slice().try_into().expect("two linear arrows"),
            label_at: p.label_at,
            rotation: p.rotation,
            label_box: p.label_box,
        }
    }
    fn write(&self, p: &mut Placed) {
        p.lines.copy_from_slice(&self.lines);
        p.arrows.copy_from_slice(&self.arrows);
        p.label_at = self.label_at;
        p.rotation = self.rotation;
        p.label_box = self.label_box;
    }
}

// Linear placements store their dimension line after the two extension lines.
// Dimensions sharing a datum must stay on separate lines when moving around a pin.
fn dimension_lines_overlap(p: &Placed, q: &Placed) -> bool {
    let (Some(a), Some(b)) = (p.lines.get(2), q.lines.get(2)) else {
        return false;
    };
    (0..2).any(|k| {
        let along = 1 - k;
        (a[0][k] - a[1][k]).abs() < 1e-6
            && (b[0][k] - b[1][k]).abs() < 1e-6
            && (a[0][k] - b[0][k]).abs() < 1e-6
            && p.arrows.iter().any(|a| {
                q.arrows
                    .iter()
                    .any(|b| (a[0][along] - b[0][along]).abs() < 1e-6)
            })
            && a[0][along]
                .min(a[1][along])
                .max(b[0][along].min(b[1][along]))
                < a[0][along]
                    .max(a[1][along])
                    .min(b[0][along].max(b[1][along]))
                    - 1e-6
    })
}

// The stray-text threshold is one dimension text height.
// Invariant: This is a compile-time equality of drawing constants, not an input assertion.
const _: () = assert!(STRAY_TEXT == TEXT);

/// #32: re-runs the stack spacing of each side an explicit placement (a proposed fix, or an
/// edit) puts linear dimensions on. An item asking for a lane another dimension's line already
/// takes over an overlapping span moves out lane by lane until its line is its own, the shorter
/// span inside (so extension lines do not cross the longer line). Pinned and offset placements
/// keep their ink; an item that collides with nothing keeps its requested lane, so a layout with
/// no shared line is unchanged. *occupied* are the automatic linear items' lines by view.
#[allow(clippy::too_many_arguments)]
pub(super) fn respace(
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    chosen: &[Option<&'static str>],
    places: &[Option<&crate::placement_intent::Place>],
    sides: &[Option<Side>],
    lanes: &[usize],
    occupied: &mut BTreeMap<&'static str, Vec<Vec<[P2; 2]>>>,
    page: &Page,
    intended: &mut [Option<Placed>],
) {
    let span = |p: &Placed| linear_ink(&p.lines).map_or(0.0, |l| l.span[1] - l.span[0]);
    let mut order: Vec<usize> = (0..items.len())
        .filter(|&i| {
            matches!(items[i].shape, Shape::Linear { .. })
                && chosen[i].is_some()
                && sides[i].is_some()
                && intended[i].is_some()
                && places[i].is_some_and(|p| !p.pinned && p.offset.is_none())
        })
        .collect();
    let requested = |i: usize| places[i].and_then(|p| p.lane).unwrap_or(lanes[i]);
    order.sort_by(|&a, &b| {
        (chosen[a], requested(a))
            .cmp(&(chosen[b], requested(b)))
            .then(
                // Invariant: The order contains only indices whose intended placement is Some.
                span(intended[a].as_ref().unwrap()).total_cmp(&span(intended[b].as_ref().unwrap())),
            )
            .then(a.cmp(&b))
    });
    for i in order {
        let (Some(v), Some(side), Some(place)) = (chosen[i], sides[i], places[i]) else {
            continue;
        };
        let taken = occupied.entry(v).or_default();
        let free = |p: &Placed| !taken.iter().any(|q| shared_dimension_line(&p.lines, q));
        // Invariant: The order contains only indices whose intended placement is Some.
        if !free(intended[i].as_ref().unwrap()) {
            // Bounded like the automatic lanes' search (`avoid_fixed`): 64 lanes out, the
            // nearest that also fits the page (on it, clear of the other views), else the
            // nearest free one, whose overrun is then reported.
            let free_lanes: Vec<Placed> = (requested(i) + 1..requested(i) + 64)
                .filter_map(|lane| {
                    let mut p = place.clone();
                    p.lane = Some(lane);
                    intent(&frames[v], &items[i], &p, side, lanes[i], 0.0).filter(|p| free(p))
                })
                .collect();
            let moved = free_lanes
                .iter()
                .find(|p| page.fits(p, v))
                .or(free_lanes.first());
            if let Some(p) = moved {
                intended[i] = Some(p.clone());
            }
        }
        // Invariant: The order contains only indices whose intended placement is Some.
        taken.push(intended[i].as_ref().unwrap().lines.clone());
    }
}

/// A held (bare-pin) placement re-laid for the item's current label: the lines stay where the
/// automatic layout put them, and only the label is re-set from *terms*. A linear or angular label
/// keeps its centre along its direction; a callout's keeps the edge at its shoulder and the
/// shoulder line follows the new width. Unchanged terms keep the held ink byte for byte.
pub(super) fn relabel(held: &Placed, was: &[String], w: &Want) -> Placed {
    if was == w.terms.as_slice() {
        return held.clone();
    }
    let ink = label_ink(&w.terms, font());
    let mut p = held.clone();
    let fresh = label_box(&ink, p.label_at, p.rotation);
    let old = held.label_box;
    let r = p.rotation.rem_euclid(180.0);
    let axis = if r.abs() < 1e-9 {
        Some(0)
    } else if (r - 90.0).abs() < 1e-9 {
        Some(1)
    } else {
        None
    };
    let mut d = [0.0; 2];
    match (&w.shape, axis) {
        (Shape::Callout { .. }, Some(k)) => {
            // The shoulder is the leader's last segment; the label sits beyond its far end.
            let shoulder = p.lines.last().copied();
            let right = shoulder.is_none_or(|[a, b]| b[k] >= a[k]);
            d[k] = if right {
                old[k] - fresh[k]
            } else {
                old[k + 2] - fresh[k + 2]
            };
        }
        (_, Some(k)) => d[k] = 0.5 * (old[k] + old[k + 2] - fresh[k] - fresh[k + 2]),
        (_, None) => {
            for k in 0..2 {
                d[k] = 0.5 * (old[k] + old[k + 2] - fresh[k] - fresh[k + 2]);
            }
        }
    }
    p.label_at = [p.label_at[0] + d[0], p.label_at[1] + d[1]];
    p.label_box = label_box(&ink, p.label_at, p.rotation);
    if let (Shape::Callout { .. }, Some(k), Some(last)) = (&w.shape, axis, p.lines.last_mut()) {
        // A shoulder that ran under the old label runs under the new one, as far past its end.
        let [a, b] = *last;
        if b[k] >= a[k] && b[k] > old[k] {
            last[1][k] = p.label_box[k + 2] + (b[k] - old[k + 2]);
        } else if b[k] < a[k] && b[k] < old[k + 2] {
            last[1][k] = p.label_box[k] + (b[k] - old[k]);
        }
    }
    p
}

/// Pin a linear label relative to the canonical ink transported into this view.
/// Keep measured stations fixed and rebuild each witness towards the new line independently.
pub(super) fn offset_intent(
    frame: &Frame,
    w: &Want,
    offset: P2,
    held: Option<(&Placed, &[String])>,
) -> Option<Placed> {
    let (held, terms) = held?;
    let mut p = relabel(held, terms, w);
    if offset.iter().all(|v| v.abs() < 1e-6) {
        return Some(p);
    }
    let radians = p.rotation.to_radians();
    let u = match p.rotation {
        0.0 => [1.0, 0.0],
        90.0 => [0.0, 1.0],
        _ => [radians.cos(), radians.sin()],
    };
    let n = [-u[1], u[0]];
    let dot = |a: P2, b: P2| a[0] * b[0] + a[1] * b[1];
    let shift = dot(offset, n);
    let line = *p.lines.get(2)?;
    let height = dot(line[0], n) + shift;
    let move_to = |a: P2, h: f64| [0, 1].map(|k| a[k] + (h - dot(a, n)) * n[k]);
    // Linear edge endpoints are silhouette stations. Choose the nearest of the two
    // feature edges, allowing a witness to approach an interior line from either side.
    let sources = match &w.shape {
        Shape::Linear {
            a,
            b,
            a_edge,
            b_edge,
            ..
        } => {
            let k = usize::from(p.rotation == 0.0);
            let target = move_to(line[0], height)[k];
            [(*a, *a_edge), (*b, *b_edge)].map(|(a, edge)| {
                let mut a = frame.page(a);
                if edge {
                    let o = frame.outline();
                    a[k] = if (target - o[k]).abs() <= (target - o[k + 2]).abs() {
                        o[k]
                    } else {
                        o[k + 2]
                    };
                }
                a
            })
        }
        Shape::Aligned { a, b } => {
            let mut sources = [frame.page(*a), frame.page(*b)];
            sources.sort_by(|a, b| dot(*a, u).total_cmp(&dot(*b, u)));
            sources
        }
        _ => return None,
    };
    p.lines.truncate(3);
    for (i, source) in sources.into_iter().enumerate() {
        let out = if height >= dot(source, n) { 1.0 } else { -1.0 };
        p.lines[i] = [
            move_to(source, dot(source, n) + out * GAP),
            move_to(source, height + out * OVERSHOOT),
        ];
    }
    // The anchor is the canonical label origin, independent of wording edits.
    p.label_at = [held.label_at[0] + offset[0], held.label_at[1] + offset[1]];
    let ink = label_ink(&w.terms, font());
    p.label_box = label_box(&ink, p.label_at, p.rotation);
    let mut tips = sources.map(|source| move_to(source, height));
    tips.sort_by(|a, b| dot(*a, u).total_cmp(&dot(*b, u)));
    let [lo, hi] = tips.map(|t| dot(t, u));
    let text_lo = dot(p.label_at, u) + ink.min[0];
    let text_hi = dot(p.label_at, u) + ink.max[0];
    let outside = text_lo < lo + ARROW + SLIDE_CLEAR || text_hi > hi - ARROW - SLIDE_CLEAR;
    let along = |a: P2, d: f64| [0, 1].map(|k| a[k] + d * u[k]);
    p.arrows = if outside {
        vec![
            arrow(tips[0], along(tips[0], -1.0)),
            arrow(tips[1], along(tips[1], 1.0)),
        ]
    } else {
        vec![
            arrow(tips[0], along(tips[0], 1.0)),
            arrow(tips[1], along(tips[1], -1.0)),
        ]
    };
    let extent = if outside {
        [
            if text_hi < lo {
                (lo - ARROW - 0.5).min(text_hi + 0.5)
            } else {
                lo - ARROW - 0.5
            },
            if text_lo > hi {
                (hi + ARROW + 0.5).max(text_lo - 0.5)
            } else {
                hi + ARROW + 0.5
            },
        ]
    } else {
        [lo, hi]
    };
    p.lines[2] = [
        along(tips[0], extent[0] - lo),
        along(tips[1], extent[1] - hi),
    ];
    Some(p)
}

/// Resolve intent independently of the other annotations, then let the solver fill around it.
pub(super) fn intent(
    frame: &Frame,
    w: &Want,
    p: &crate::placement_intent::Place,
    side: Side,
    lane: usize,
    callout_width: f64,
) -> Option<Placed> {
    if !p.pinned && p.side.is_none() && p.lane.is_none() && p.along.is_none() {
        return None;
    }
    if matches!(w.shape, Shape::Callout { .. }) && crate::placement_intent::side_hint(p).is_some() {
        return None;
    }
    let side = match p.side {
        Some(crate::placement_intent::Side::Above) => Side::Above,
        Some(crate::placement_intent::Side::Below) => Side::Below,
        Some(crate::placement_intent::Side::Left) => Side::Left,
        Some(crate::placement_intent::Side::Right) => Side::Right,
        None => side,
    };
    let lane = p.lane.unwrap_or(lane);
    let mut placed = match &w.shape {
        Shape::Aligned { a, b } => place_aligned(frame, *a, *b, &w.terms, side, lane)?,
        Shape::Linear { axis, .. } => {
            let horizontal = across(frame, *axis)?;
            if horizontal != matches!(side, Side::Above | Side::Below) {
                return None;
            }
            place_linear(frame, &w.shape, &w.terms, side, lane)?
        }
        Shape::Callout { sites, arcs } => {
            let (site, _) = sites.first()?;
            let c = frame.page(*site);
            let o = frame.outline();
            let (mut line, out) = lane_at(o, side, 0);
            let ink = label_ink(&w.terms, font());
            let width = ink.max[0] - ink.min[0];
            // Horizontal callout labels need their measured width in a side column. Use the
            // canonical width so editing another item's text cannot move a pin's lane.
            let step = callout_step(side, width, callout_width);
            line += out * lane as f64 * step;
            let k = usize::from(matches!(side, Side::Above | Side::Below));
            let f = p
                .along
                .unwrap_or(crate::placement_intent::Along::Fraction(0.5))
                .fraction();
            let mut elbow = c;
            elbow[k] = line;
            elbow[1 - k] = o[1 - k] + f * (o[3 - k] - o[1 - k]);
            let tip = super::leaders::intent_tip(frame, sites, arcs, elbow);
            let left = match side {
                Side::Left => true,
                Side::Right => false,
                Side::Above | Side::Below => p.pinned && elbow[0] < tip[0] - 1e-9,
            };
            let end = [
                elbow[0]
                    + if left {
                        -width - super::SHOULDER
                    } else {
                        width + super::SHOULDER
                    },
                elbow[1],
            ];
            let at = [
                if left {
                    end[0] - ink.min[0]
                } else {
                    elbow[0] + super::SHOULDER - ink.min[0]
                },
                elbow[1] + LIFT,
            ];
            Placed {
                lines: vec![[tip, elbow], [elbow, end]],
                arrows: vec![arrow(tip, elbow)],
                label_at: at,
                rotation: 0.0,
                label_box: label_box(&ink, at, 0.0),
            }
        }
        Shape::Angular { angle, .. } => super::angular::intent(frame, angle, &w.terms, p)?,
    };
    if matches!(w.shape, Shape::Linear { .. })
        && let Some(along) = p.along
    {
        let k = usize::from(placed.rotation != 0.0);
        let lo = placed
            .arrows
            .iter()
            .map(|a| a[0][k])
            .fold(f64::INFINITY, f64::min);
        let hi = placed
            .arrows
            .iter()
            .map(|a| a[0][k])
            .fold(f64::NEG_INFINITY, f64::max);
        let ink = label_ink(&w.terms, font());
        let width = ink.max[0] - ink.min[0];
        if along == crate::placement_intent::Along::Slot(crate::placement_intent::Slot::Outside) {
            // #32: `outside` sets the label just past the line's far end, the line run on to it,
            // as a span too short for its label has it (`place_linear_at`); 1.25 of a long span
            // stranded the text far from its line.
            let end = (hi + ARROW + 2.0).max(placed.lines[2][0][k].max(placed.lines[2][1][k]));
            placed.label_at[k] = end + 1.0 - ink.min[0];
            let line = &mut placed.lines[2];
            let far = usize::from(line[1][k] >= line[0][k]);
            line[far][k] = end;
        } else {
            placed.label_at[k] = lo + along.fraction() * (hi - lo) - 0.5 * width - ink.min[0];
        }
        placed.label_box = label_box(&ink, placed.label_at, placed.rotation);
    }
    Some(placed)
}

/// Places each item merged away ([`owners`]) in its survivor's lane, once everything else is
/// placed (callouts included), and moves its label as [`settle`] would, clear of every other
/// item but its survivor and the items merged with it: their ink is the one span drawn once. One
/// [`separate`] moved to a lane of its own (*alone*) keeps clear of every other item. The others
/// never see it, so it moves nothing (it is drawn only when the spec omits its survivor or breaks
/// the merge).
#[allow(clippy::too_many_arguments)]
pub(super) fn place_merged(
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    owner: &[Option<usize>],
    alone: &[bool],
    drawn: &[bool],
    sides: &[Option<Side>],
    lanes: &[usize],
    placed: &mut [Option<Placed>],
    page: &Page,
    slot_layout: bool,
) {
    for i in 0..items.len() {
        if owner[i].is_some() && matches!(items[i].shape, Shape::Linear { .. }) {
            placed[i] = views_of[i]
                .zip(sides[i])
                .and_then(|(v, side)| place(&frames[v], &items[i], side, lanes[i]));
        } else if owner[i].is_some() && !drawn[i] {
            // Covered callouts leave no phantom route. A promoted callout was
            // already solved by the shared leader inventory and keeps that ink.
            placed[i] = None;
        }
    }
    settle_where(
        placed,
        page,
        slot_layout,
        |i| owner[i].is_some() && matches!(items[i].shape, Shape::Linear { .. }),
        |i, j| j != i && (alone[i] || (owner[j].is_none() && Some(j) != owner[i])),
    );
}

/// [`settle`] over the items *moves* picks, each against the others *sees* picks for it.
fn settle_where(
    items: &mut [Option<Placed>],
    page: &Page,
    slot_layout: bool,
    moves: impl Fn(usize) -> bool,
    sees: impl Fn(usize, usize) -> bool,
) {
    let text_box = |b| {
        if slot_layout {
            super::contacts::clear_box(b)
        } else {
            b
        }
    };
    for i in 0..items.len() {
        if !moves(i) {
            continue;
        }
        let Some(p) = items[i].as_ref() else {
            continue;
        };
        let others = || {
            items
                .iter()
                .enumerate()
                .filter(|(j, _)| sees(i, *j))
                .filter_map(|(_, q)| q.as_ref())
        };
        // A linear dimension's own lines are fixed by what it measures, so only a line run on to
        // its label must clear other labels.
        let clear = |c: &Placed| {
            let own: Vec<&[P2; 2]> = c.lines.iter().filter(|l| !p.lines.contains(l)).collect();
            others().all(|q| {
                !q.lines.iter().any(|l| crosses(*l, text_box(c.label_box)))
                    && !boxes_overlap(c.label_box, text_box(q.label_box))
                    && !own.iter().any(|l| crosses(**l, text_box(q.label_box)))
            })
        };
        if clear(p) {
            continue;
        }
        // A new place stays on the sheet (unless the item was off it already, which stays
        // reported), its label off the views and the title block, and its lines out of the
        // title block.
        let off_already = !page.on_sheet(p);
        let linear = p.arrows.len() == 2
            && p.lines.len() >= 3
            && (p.arrows[0][0][usize::from(p.rotation == 0.)]
                - p.arrows[1][0][usize::from(p.rotation == 0.)])
            .abs()
                < 1e-6;
        let outward = linear.then(|| super::label_lanes::outward_heads(p));
        let peers: Vec<_> = others().collect();
        let found = slides(p, page.drawable)
            .chain(outward.iter().flat_map(|outward| {
                super::label_lanes::alternatives_placed(outward, page.drawable, &peers)
            }))
            .find(|c| {
                (off_already || page.on_sheet(c))
                    && page.clear_of_furniture(c)
                    && clear(c)
                    && c.arrows
                        .iter()
                        .flat_map(|a| super::leaders::head(*a))
                        .all(|s| peers.iter().all(|q| !crosses(s, q.label_box)))
            });
        if let Some(c) = found {
            items[i] = Some(c);
        }
    }
}

/// A linear dimension's other label places along its line, nearest first (see [`settle`]),
/// made as they are tried: a line far longer than the sheet (a part drawn at a scale too large
/// for it) has more places than memory holds.
pub(super) fn slide_parameters(
    p: &Placed,
    drawable: [f64; 4],
) -> Box<dyn Iterator<Item = (f64, [f64; 2])> + '_> {
    // This solver slides x or y alone; oblique geometry is placed in its own frame.
    if !(p.rotation == 0.0 || p.rotation.abs() == 90.0) {
        return Box::new(std::iter::empty());
    }
    // Along the line: x for a horizontal dimension, y for a vertical one.
    let k = usize::from(p.rotation != 0.0);
    let Some(&line) = p.lines.get(2) else {
        return Box::new(std::iter::empty());
    };
    let (l0, l1) = (line[0][k].min(line[1][k]), line[0][k].max(line[1][k]));
    let b = p.label_box;
    let width = b[k + 2] - b[k];
    // Each a shift of the label and the line's new extent.
    let (lo, hi) = (l0 + ARROW + SLIDE_CLEAR, l1 - ARROW - SLIDE_CLEAR);
    let shifts: Box<dyn Iterator<Item = (f64, [f64; 2])>> = if b[k] >= lo && b[k + 2] <= hi {
        // Only shifts that keep the label on the drawing area along the line: their count is
        // bounded by the sheet, however long the line (a part drawn at a scale far too large).
        let (lo, hi) = (lo.max(drawable[k]), hi.min(drawable[k + 2]));
        let (d_min, d_max) = (lo - b[k], hi - b[k + 2]);
        // The steps n >= 1 for which +n or -n steps may lie in [d_min, d_max], a step wider.
        let up = (
            (d_min / SLIDE_STEP).floor().max(1.0),
            (d_max / SLIDE_STEP).ceil(),
        );
        let down = (
            (-d_max / SLIDE_STEP).floor().max(1.0),
            (-d_min / SLIDE_STEP).ceil(),
        );
        let spans: Vec<(f64, f64)> = [up, down].into_iter().filter(|s| s.0 <= s.1).collect();
        let first = spans.iter().map(|s| s.0).fold(f64::INFINITY, f64::min);
        let last = spans.iter().map(|s| s.1).fold(f64::NEG_INFINITY, f64::max);
        let (first, last) = if first <= last {
            (first as i64, last as i64)
        } else {
            (1, 0)
        };

        Box::new(
            (first..=last)
                .flat_map(|n| [n, -n])
                .map(|n| n as f64 * SLIDE_STEP)
                .filter(move |d| b[k] + d >= lo && b[k + 2] + d <= hi)
                .map(move |d| (d, [l0, l1])),
        )
    } else if b[k] > l1 {
        // Beyond the far end, 1 mm past the line; the near end mirrors it.
        let back = (l0 - 1.0 - width) - b[k];
        Box::new((0..=(SLIDE_REACH / SLIDE_STEP) as i64).flat_map(move |n| {
            let d = n as f64 * SLIDE_STEP;
            (n > 0)
                .then_some((d, [l0, b[k] + d - 1.0]))
                .into_iter()
                .chain([(back - d, [b[k + 2] + back - d + 1.0, l1])])
        }))
    } else {
        Box::new(std::iter::empty())
    };
    shifts
}

pub(super) fn slides(p: &Placed, drawable: [f64; 4]) -> Box<dyn Iterator<Item = Placed> + '_> {
    let k = usize::from(p.rotation != 0.0);
    let shifts = slide_parameters(p, drawable);
    Box::new(shifts.map(move |(d, extent)| {
        let mut c = p.clone();
        c.label_box[k] += d;
        c.label_box[k + 2] += d;
        c.label_at[k] += d;
        c.lines[2][0][k] = extent[0];
        c.lines[2][1][k] = extent[1];
        c
    }))
}

/// The leader's tip: on the rim, at 45° towards the strip and right, of the site nearest the
/// strip's right-hand end; and that site's radius on the page.
fn callout_tip(
    frame: &Frame,
    sites: &[(P3, f64)],
    arcs: &[crate::arc::ProfileArc],
    up: bool,
) -> (P2, f64) {
    let r = std::f64::consts::FRAC_1_SQRT_2;
    let v = if up { 1.0 } else { -1.0 };
    sites
        .iter()
        .map(|(c, radius)| {
            let p = frame.page(*c);
            let rr = frame.s * radius;
            let tip = if arcs.is_empty() {
                [p[0] + r * rr, p[1] + v * r * rr]
            } else {
                super::leaders::intent_tip(
                    frame,
                    sites,
                    arcs,
                    [p[0] + r * (rr + 1.0), p[1] + v * r * (rr + 1.0)],
                )
            };
            ((tip, rr), p[0] + v * p[1])
        })
        .max_by(|a, b| a.1.total_cmp(&b.1))
        // Invariant: Callout wants are admitted only with at least one proven site.
        .expect("a callout has a site")
        .0
}

/// Every extreme point of a placed item's ink: its lines' ends, its arrowheads' corners (as
/// `dims::render` draws them) and its label box's corners.
pub(super) fn ink_points(p: &Placed) -> impl Iterator<Item = P2> + '_ {
    let heads = p.arrows.iter().flat_map(|[tip, tail]| {
        let n = [tail[1] - tip[1], tip[0] - tail[0]];
        let k = ARROW_HALF / n[0].hypot(n[1]).max(1e-12);
        [
            *tip,
            [tail[0] + k * n[0], tail[1] + k * n[1]],
            [tail[0] - k * n[0], tail[1] - k * n[1]],
        ]
    });
    let b = p.label_box;
    p.lines
        .iter()
        .flat_map(|l| l.iter().copied())
        .chain(heads)
        .chain([[b[0], b[1]], [b[2], b[1]], [b[0], b[3]], [b[2], b[3]]])
}

/// Whether placed item *p*'s rendered ink reaches into the open box *b* (ISSUE-367-a): its
/// lines and each arrowhead's filled flare as `dims::render` draws it, not only the shaft. The
/// flare is tested where it is, at the tip, not along the whole shaft. The stroke's width (the
/// annotation layer's 0.05 mm) is not added: a graze that thin flipped sheet choices (a
/// GD&T leader 0.01 mm under a label moved hw_string_post_tapped_intent from A3 5:1 to A4 2:1)
/// without a contact a reader can see.
pub(super) fn ink_through(p: &Placed, b: [f64; 4]) -> bool {
    if p.lines.iter().any(|s| crosses(*s, b)) {
        return true;
    }
    p.arrows.iter().any(|&[tip, tail]| {
        let n = [tail[1] - tip[1], tip[0] - tail[0]];
        let k = ARROW_HALF / n[0].hypot(n[1]).max(1e-12);
        let a = [tail[0] + k * n[0], tail[1] + k * n[1]];
        let c = [tail[0] - k * n[0], tail[1] - k * n[1]];
        let edges = [[tip, a], [a, c], [c, tip]];
        // An edge through the box, or the box wholly inside the filled head.
        let mid = [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])];
        let side =
            |[u, v]: [P2; 2]| (v[0] - u[0]) * (mid[1] - u[1]) - (v[1] - u[1]) * (mid[0] - u[0]);
        let within = {
            let s = edges.map(side);
            s.iter().all(|x| *x > 0.0) || s.iter().all(|x| *x < 0.0)
        };
        edges.iter().any(|e| crosses(*e, b)) || within
    })
}

pub(super) fn boxes_overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
}

/// Whether segment *s* passes through the open box *b*.
pub(super) fn crosses(s: [P2; 2], b: [f64; 4]) -> bool {
    let (p, q) = (s[0], s[1]);
    // Wholly to one side of the box: the clipping below finds nothing there either (each bound
    // it divides out is then at least 1 or below 0), so the answer is the same, found sooner.
    if (p[0] < b[0] && q[0] < b[0])
        || (p[0] > b[2] && q[0] > b[2])
        || (p[1] < b[1] && q[1] < b[1])
        || (p[1] > b[3] && q[1] > b[3])
    {
        return false;
    }
    // Liang–Barsky clipping against the box.
    let d = [q[0] - p[0], q[1] - p[1]];
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (num, den) in [
        (p[0] - b[0], -d[0]),
        (b[2] - p[0], d[0]),
        (p[1] - b[1], -d[1]),
        (b[3] - p[1], d[1]),
    ] {
        if den == 0.0 {
            if num <= 0.0 {
                return false;
            }
        } else {
            let t = num / den;
            if den < 0.0 {
                t0 = t0.max(t);
            } else {
                t1 = t1.min(t);
            }
        }
    }
    t1 - t0 > 1e-9
}

fn inside(p: P2, b: [f64; 4]) -> bool {
    p[0] >= b[0] - 1e-9 && p[0] <= b[2] + 1e-9 && p[1] >= b[1] - 1e-9 && p[1] <= b[3] + 1e-9
}

/// The sheet's drawing area, inside its margin.
fn drawable(sheet: &PlannedSheet) -> [f64; 4] {
    let [w, h] = sheet.page;
    [margin(), margin(), w - margin(), h - margin()]
}

/// Each view's drawn box on the page: a principal view's projected envelope, the isometric's
/// projected bounds.
fn outlines<'a>(analysis: &Analysis, views: &'a [PlannedView]) -> Vec<(&'a str, [f64; 4])> {
    views
        .iter()
        .map(|v| {
            let b = if v.id == "v:iso" {
                let ib = analysis.drawing_iso_bounds();
                [
                    v.at[0] + v.scale * ib[0],
                    v.at[1] + v.scale * ib[1],
                    v.at[0] + v.scale * ib[2],
                    v.at[1] + v.scale * ib[3],
                ]
            } else {
                Frame::new(analysis, v).outline
            };
            (v.id.as_str(), b)
        })
        .collect()
}

/// What a placed item's ink must stay inside or off on the page: the drawing area, each view's
/// drawn box ([`outlines`]) and the title block.
#[derive(serde::Serialize)]
pub(super) struct Page<'a> {
    drawable: [f64; 4],
    views: Vec<(&'a str, [f64; 4])>,
    /// Each view's reserved area (its box and its annotation bands), by its ID.
    areas: Vec<(&'a str, [f64; 4])>,
    title_block: [f64; 4],
}

impl<'a> Page<'a> {
    pub(super) fn new(
        analysis: &Analysis,
        sheet: &PlannedSheet,
        views: &'a [PlannedView],
    ) -> Page<'a> {
        Page {
            drawable: drawable(sheet),
            views: outlines(analysis, views)
                .into_iter()
                .filter(|(id, _)| views.iter().any(|v| v.id == *id && v.sheet == sheet.number))
                .collect(),
            areas: views
                .iter()
                .filter(|v| v.sheet == sheet.number)
                .map(|v| (v.id.as_str(), v.area))
                .collect(),
            title_block: sheet.title_block,
        }
    }

    /// The reserved area of the view named *view* (`plan`), when the sheet has it.
    pub(super) fn area(&self, view: &str) -> Option<[f64; 4]> {
        self.areas
            .iter()
            .find(|(id, _)| id.strip_prefix("v:") == Some(view))
            .map(|(_, a)| *a)
    }

    pub(super) fn drawable(&self) -> [f64; 4] {
        self.drawable
    }

    pub(super) fn title_block(&self) -> [f64; 4] {
        self.title_block
    }

    /// Each view's drawn box, by its ID (`v:plan`).
    pub(super) fn views(&self) -> &[(&'a str, [f64; 4])] {
        &self.views
    }

    fn on_sheet(&self, p: &Placed) -> bool {
        ink_points(p).all(|q| inside(q, self.drawable))
    }

    /// The immutable witness strokes and heads. Sliding text can shorten a
    /// dimension line's run-on, but cannot repair these physical endpoints.
    fn witnesses_fit(&self, p: &Placed, own: &str) -> bool {
        p.lines
            .iter()
            .take(2)
            .flatten()
            .copied()
            .chain(
                p.arrows
                    .iter()
                    .flat_map(|a| super::leaders::head(*a).into_iter().flatten()),
            )
            .all(|q| inside(q, self.drawable))
            && p.lines.iter().take(2).all(|line| {
                !crosses(*line, self.title_block)
                    && !self
                        .views
                        .iter()
                        .any(|(id, b)| id.strip_prefix("v:") != Some(own) && crosses(*line, *b))
            })
    }

    /// The views other than *own* (a view's name, `plan`) that a line of *p* runs across.
    fn crossed<'b>(&'b self, p: &'b Placed, own: &'b str) -> impl Iterator<Item = &'a str> + 'b {
        self.views
            .iter()
            .filter(move |(id, b)| {
                id.strip_prefix("v:") != Some(own) && p.lines.iter().any(|l| crosses(*l, *b))
            })
            .map(|(id, _)| *id)
    }

    /// Whether *p*, an item of view *own*, fits the page: its ink on the sheet, its label off
    /// every view and the title block, and no line through the title block or another view.
    pub(super) fn fits(&self, p: &Placed, own: &str) -> bool {
        self.on_sheet(p) && self.clear_of_furniture(p) && self.crossed(p, own).next().is_none()
    }

    pub(super) fn fits_interior(&self, p: &Placed, own: &str) -> bool {
        self.on_sheet(p)
            && !boxes_overlap(p.label_box, self.title_block)
            && !p.lines.iter().any(|l| crosses(*l, self.title_block))
            && self.crossed(p, own).next().is_none()
            && self.views.iter().all(|(id, b)| {
                id.strip_prefix("v:") == Some(own) || !boxes_overlap(p.label_box, *b)
            })
    }

    /// Whether *p*'s label is off every view and the title block, and no line of it runs through
    /// the title block.
    fn clear_of_furniture(&self, p: &Placed) -> bool {
        !boxes_overlap(p.label_box, self.title_block)
            && self
                .views
                .iter()
                .all(|(_, b)| !boxes_overlap(p.label_box, *b))
            && !p.lines.iter().any(|l| crosses(*l, self.title_block))
    }
}

/// Includes the filled arrowheads' edges, rather than only their centre shafts.
fn dimension_segments(p: &Placed) -> impl Iterator<Item = [P2; 2]> + '_ {
    p.lines
        .iter()
        .copied()
        .chain(p.arrows.iter().flat_map(|a| super::leaders::head(*a)))
}

pub(super) fn dimension_lines_clear(a: &Placed, b: &Placed) -> bool {
    dimension_segments(a).all(|a| dimension_segments(b).all(|b| !super::leaders::lines_meet(a, b)))
}

/// [`dimension_lines_clear`], except that *a* may cross the first *crossable* lines of *b*
/// (its extension lines, or every line of a chain line) transversely; it may still never run
/// along or end on them.
pub(super) fn lines_clear_but(a: &Placed, b: &Placed, crossable: usize) -> bool {
    dimension_segments(a).all(|s| {
        b.lines.iter().enumerate().all(|(k, t)| {
            !super::leaders::lines_meet(s, *t)
                || (k < crossable && super::leaders::segments_cross(s, *t))
        }) && b
            .arrows
            .iter()
            .flat_map(|h| super::leaders::head(*h))
            .all(|t| !super::leaders::lines_meet(s, t))
    })
}

pub(super) fn annotations_clear(a: &Placed, b: &Placed) -> bool {
    !boxes_overlap(a.label_box, b.label_box)
        && !ink_through(a, b.label_box)
        && !ink_through(b, a.label_box)
}

/// Make room for a compact candidate by moving only unpinned, ordinary linear
/// dimensions. Their usual policy permits transverse witness crossings, but no
/// shared strokes or ink through labels; the new dimension clears every stroke.
#[allow(clippy::too_many_arguments)]
pub(super) fn make_room(
    i: usize,
    candidate: &Placed,
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    sides: &[Option<Side>],
    fixed: &[bool],
    placed: &[Option<Placed>],
    obstacles: &[PlannedDimension],
    page: &Page,
) -> Option<Vec<(usize, Placed)>> {
    let mut room = Room {
        root: i,
        candidate,
        frames,
        items,
        views_of,
        sides,
        fixed,
        obstacles,
        page,
    };
    let strict =
        |p: &Placed| annotations_clear(candidate, p) && dimension_lines_clear(candidate, p);
    if obstacles
        .iter()
        .filter_map(|d| d.placed.as_ref())
        .any(|p| !strict(p))
    {
        return None;
    }
    let blockers: Vec<_> = placed
        .iter()
        .enumerate()
        .filter_map(|(j, p)| (i != j && p.as_ref().is_some_and(|p| !strict(p))).then_some(j))
        .collect();
    let mut work = placed.to_vec();
    work[i] = Some(candidate.clone());
    let mut locked = fixed.to_vec();
    locked[i] = true;
    for j in blockers {
        if !room.relocate(j, &mut work, &mut locked, 4) {
            return None;
        }
    }
    Some(
        work.into_iter()
            .enumerate()
            .filter_map(|(j, p)| {
                (i != j && p != placed[j])
                    .then(|| p.map(|p| (j, p)))
                    .flatten()
            })
            .collect(),
    )
}

struct Room<'a, 'p> {
    root: usize,
    candidate: &'a Placed,
    frames: &'a BTreeMap<&'a str, Frame>,
    items: &'a [Want],
    views_of: &'a [Option<&'static str>],
    sides: &'a [Option<Side>],
    fixed: &'a [bool],
    obstacles: &'a [PlannedDimension],
    page: &'a Page<'p>,
}

impl Room<'_, '_> {
    fn relocate(
        &mut self,
        j: usize,
        work: &mut Vec<Option<Placed>>,
        locked: &mut Vec<bool>,
        depth: usize,
    ) -> bool {
        if depth == 0
            || locked[j]
            || self.fixed[j]
            || !matches!(self.items[j].shape, Shape::Linear { .. })
        {
            return false;
        }
        let (Some(view), Some(side)) = (self.views_of[j], self.sides[j]) else {
            return false;
        };
        let clear_root = |p: &Placed| {
            annotations_clear(self.candidate, p) && dimension_lines_clear(self.candidate, p)
        };
        let compatible = |p: &Placed, q: &Placed| {
            annotations_clear(p, q)
                && !dimension_segments(p)
                    .any(|a| dimension_segments(q).any(|b| super::leaders::segments_along(a, b)))
        };
        let mut candidates: Vec<_> = (0..12)
            .flat_map(|lane| {
                [side, opposite(side)]
                    .into_iter()
                    .map(move |side| (side, lane))
            })
            .filter_map(|(side, lane)| place(&self.frames[view], &self.items[j], side, lane))
            .filter(|p| {
                self.page.fits(p, view)
                    && clear_root(p)
                    && self
                        .obstacles
                        .iter()
                        .filter_map(|d| d.placed.as_ref())
                        .all(|q| compatible(p, q))
            })
            .map(|p| {
                let blockers: Vec<_> = work
                    .iter()
                    .enumerate()
                    .filter_map(|(k, q)| {
                        (k != j && k != self.root && q.as_ref().is_some_and(|q| !compatible(&p, q)))
                            .then_some(k)
                    })
                    .collect();
                let crossings = work
                    .iter()
                    .flatten()
                    .map(|q| {
                        dimension_segments(&p)
                            .filter(|a| {
                                dimension_segments(q).any(|b| super::leaders::segments_cross(*a, b))
                            })
                            .count()
                    })
                    .sum::<usize>();
                (blockers, crossings, p)
            })
            .collect();
        candidates.sort_by_key(|(blockers, crossings, _)| (blockers.len(), *crossings));
        for (blockers, _, p) in candidates {
            if blockers.iter().any(|&k| locked[k]) {
                continue;
            }
            let saved = work.clone();
            let saved_locks = locked.clone();
            locked[j] = true;
            work[j] = Some(p);
            if blockers
                .into_iter()
                .all(|k| self.relocate(k, work, locked, depth - 1))
            {
                return true;
            }
            *work = saved;
            *locked = saved_locks;
        }
        false
    }
}

/// Search the perpendicular offsets of an aligned span against the settled ink.
/// A side/lane hint is kept when clear; only a pin can require colliding ink.
#[allow(clippy::too_many_arguments)]
pub(super) fn clear_aligned(
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    sides: &[Option<Side>],
    fixed: &[bool],
    placed: &mut [Option<Placed>],
    obstacles: &[PlannedDimension],
    page: &Page,
) {
    for (i, w) in items.iter().enumerate() {
        let (Shape::Aligned { a, b }, Some(view), Some(side)) = (&w.shape, views_of[i], sides[i])
        else {
            continue;
        };
        if fixed[i] || placed[i].is_none() {
            continue;
        }
        let frame = &frames[view];
        let others: Vec<_> = placed
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .filter_map(|(_, p)| p.as_ref())
            .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
            .collect();
        let clear = |p: &Placed| {
            page.fits(p, view)
                && others.iter().all(|q| {
                    dimension_lines_clear(p, q)
                        && !boxes_overlap(p.label_box, q.label_box)
                        && !dimension_segments(p).any(|s| crosses(s, q.label_box))
                        && !dimension_segments(q).any(|s| crosses(s, p.label_box))
                })
        };
        if placed[i].as_ref().is_some_and(clear) {
            continue;
        }
        let found = (0..80).find_map(|step| {
            [side, opposite(side)].into_iter().find_map(|side| {
                aligned_at_offset(
                    frame.page(*a),
                    frame.page(*b),
                    frame.outline(),
                    &w.terms,
                    side,
                    GAP + step as f64,
                )
                .and_then(|p| {
                    if !page.fits(&p, view) {
                        return None;
                    }
                    let moves = make_room(
                        i, &p, frames, items, views_of, sides, fixed, placed, obstacles, page,
                    )?;
                    Some((p, moves))
                })
            })
        });
        if let Some((p, moves)) = found {
            for (j, q) in moves {
                placed[j] = Some(q);
            }
            placed[i] = Some(p);
        }
    }
}

/// An aligned dimension's projected witnesses and display, before page scale and view position.
/// Layout measures its real rotated ink at each scale instead of estimating an axis-aligned lane.
#[derive(Clone, Debug, PartialEq)]
pub struct AlignedDemand {
    view: &'static str,
    a: P2,
    b: P2,
    outline: [f64; 4],
    terms: Vec<String>,
    side: Side,
    lane: usize,
}

impl AlignedDemand {
    pub(super) fn new(
        frame: &Frame,
        w: &Want,
        view: &'static str,
        side: Side,
        lane: usize,
    ) -> Self {
        let Shape::Aligned { a, b } = w.shape else {
            // Invariant: Aligned demand is constructed only for Shape::Aligned.
            unreachable!("only an aligned dimension has aligned demand")
        };
        Self {
            view,
            a: frame.page(a),
            b: frame.page(b),
            outline: frame.outline(),
            terms: w.terms.clone(),
            side,
            lane,
        }
    }

    pub(crate) fn reserve(&self, strips: &mut crate::layout::Strips, scale: f64) {
        let outline = self.outline.map(|v| v * scale);
        let Some(p) = place_aligned_page(
            self.a.map(|v| v * scale),
            self.b.map(|v| v * scale),
            outline,
            &self.terms,
            self.side,
            self.lane,
        ) else {
            return;
        };
        let bounds = ink_points(&p).fold(
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
        );
        let [left, below, right, above] = [
            outline[0] - bounds[0],
            outline[1] - bounds[1],
            bounds[2] - outline[2],
            bounds[3] - outline[3],
        ]
        .map(|v| if v > 0.0 { v + GAP } else { 0.0 });
        if self.view == "side" {
            strips.sv_left = strips.sv_left.max(left);
            strips.sv_right = strips.sv_right.max(right);
            strips.sv_top = strips.sv_top.max(above);
            strips.sv_bottom = strips.sv_bottom.max(below);
            // Front and side share the same projection row.
            strips.fv_top = strips.fv_top.max(above);
            strips.fv_bottom = strips.fv_bottom.max(below);
        } else {
            strips.left = strips.left.max(left);
            strips.right = strips.right.max(right);
            if self.view == "plan" {
                strips.pv_authored_top = strips.pv_authored_top.max(above);
                strips.pv_bottom = strips.pv_bottom.max(below);
            } else {
                strips.fv_top = strips.fv_top.max(above);
                strips.fv_bottom = strips.fv_bottom.max(below);
            }
        }
    }
}

/// Aligned ISO 129 dimension: perpendicular witnesses, exact projected station span.
fn place_aligned(
    frame: &Frame,
    a: P3,
    b: P3,
    terms: &[String],
    side: Side,
    lane: usize,
) -> Option<Placed> {
    place_aligned_page(
        frame.page(a),
        frame.page(b),
        frame.outline(),
        terms,
        side,
        lane,
    )
}

fn place_aligned_page(
    a: P2,
    b: P2,
    o: [f64; 4],
    terms: &[String],
    side: Side,
    lane: usize,
) -> Option<Placed> {
    aligned_at_offset(
        a,
        b,
        o,
        terms,
        side,
        first_line() + lane as f64 * lane_pitch(),
    )
}

fn aligned_at_offset(
    mut a: P2,
    mut b: P2,
    o: [f64; 4],
    terms: &[String],
    side: Side,
    offset: f64,
) -> Option<Placed> {
    if b[0] < a[0] || (b[0] == a[0] && b[1] < a[1]) {
        std::mem::swap(&mut a, &mut b);
    }
    let span = (b[0] - a[0]).hypot(b[1] - a[1]);
    if span <= 1e-9 {
        return None;
    }
    let u = [(b[0] - a[0]) / span, (b[1] - a[1]) / span];
    let mut n = [-u[1], u[0]];
    let outward = match side {
        Side::Above => [0.0, 1.0],
        Side::Below => [0.0, -1.0],
        Side::Left => [-1.0, 0.0],
        Side::Right => [1.0, 0.0],
    };
    if n[0] * outward[0] + n[1] * outward[1] < 0.0 {
        n = n.map(|x| -x);
    }
    let dot = |p: P2| p[0] * n[0] + p[1] * n[1];
    let outer = [[o[0], o[1]], [o[0], o[3]], [o[2], o[1]], [o[2], o[3]]]
        .map(dot)
        .into_iter()
        .fold(f64::NEG_INFINITY, f64::max);
    let height = outer + offset;
    let move_to = |p: P2, h: f64| [0, 1].map(|k| p[k] + (h - dot(p)) * n[k]);
    let (pa, pb) = (move_to(a, height), move_to(b, height));
    let ink = label_ink(terms, font());
    let width = ink.max[0] - ink.min[0];
    let along = |p: P2, s: f64| [0, 1].map(|k| p[k] + s * u[k]);
    let mut lines = vec![
        [move_to(a, dot(a) + GAP), move_to(a, height + OVERSHOOT)],
        [move_to(b, dot(b) + GAP), move_to(b, height + OVERSHOOT)],
    ];
    let arrows = if span >= width + 2.0 * ARROW + 2.0 {
        lines.push([pa, pb]);
        vec![arrow(pa, along(pa, 1.0)), arrow(pb, along(pb, -1.0))]
    } else {
        lines.push([along(pa, -ARROW - 2.0), along(pb, ARROW + 2.0)]);
        vec![arrow(pa, along(pa, -1.0)), arrow(pb, along(pb, 1.0))]
    };
    // Python's Dimension keeps the label centred independently of outside arrows.
    let start = 0.5 * (span - width) - ink.min[0];
    let label_at = [
        pa[0] + start * u[0] - LIFT * u[1],
        pa[1] + start * u[1] + LIFT * u[0],
    ];
    let rotation = u[1].atan2(u[0]).to_degrees();
    // Long centred text can reach across a witness. Clip that witness around the
    // text in the dimension's own frame, as Python's above-line Dimension does.
    let normal = [-u[1], u[0]];
    let text = [
        start + ink.min[0] - GAP,
        start + ink.max[0] + GAP,
        LIFT + ink.min[1] - GAP,
        LIFT + ink.max[1] + GAP,
    ];
    let mut continuations = Vec::new();
    for (i, t) in [0.0, span].into_iter().enumerate() {
        if t < text[0] || t > text[1] {
            continue;
        }
        let line = lines[i];
        let coordinate = |p: P2| (p[0] - pa[0]) * normal[0] + (p[1] - pa[1]) * normal[1];
        let (a, b) = (coordinate(line[0]), coordinate(line[1]));
        let mut cut = [(text[2] - a) / (b - a), (text[3] - a) / (b - a)];
        cut.sort_by(f64::total_cmp);
        let (lo, hi) = (cut[0].max(0.0), cut[1].min(1.0));
        if lo >= hi {
            continue;
        }
        let at = |t: f64| [0, 1].map(|k| line[0][k] + t * (line[1][k] - line[0][k]));
        if lo > 1e-9 {
            lines[i] = [line[0], at(lo)];
            if hi < 1.0 - 1e-9 {
                continuations.push([at(hi), line[1]]);
            }
        } else {
            lines[i] = [at(hi), line[1]];
        }
    }
    lines.extend(continuations);
    Some(Placed {
        lines,
        arrows,
        label_at,
        rotation,
        label_box: label_box(&ink, label_at, rotation),
    })
}

#[cfg(test)]
pub(super) fn collisions(
    analysis: &Analysis,
    dims: &[PlannedDimension],
    sheet: &PlannedSheet,
    views: &[PlannedView],
) -> Vec<Problem> {
    collisions_cached(
        analysis,
        dims,
        sheet,
        views,
        &super::footprints::Cache::default(),
    )
}

/// A placed item as a problem's message names it ([`crate::wording::item`]).
fn named(d: &PlannedDimension) -> String {
    crate::wording::item(&d.id, &d.kind, &d.label)
}

pub(crate) fn collisions_cached(
    analysis: &Analysis,
    dims: &[PlannedDimension],
    sheet: &PlannedSheet,
    views: &[PlannedView],
    cache: &super::footprints::Cache,
) -> Vec<Problem> {
    collisions_impl(analysis, dims, sheet, views, cache, None)
}

/// The same predicates, restricted to findings a changed item can affect.
/// Peers remain available as obstacles; unchanged peer/peer pairs need no recheck.
pub(super) fn collisions_for(
    analysis: &Analysis,
    dims: &[PlannedDimension],
    sheet: &PlannedSheet,
    views: &[PlannedView],
    cache: &super::footprints::Cache,
    id: &str,
) -> Vec<Problem> {
    collisions_impl(analysis, dims, sheet, views, cache, Some(id))
}

/// Ordered leader, arrow and total crossing counts for a rim route. Peers must be on
/// the same sheet. This is a cheap rejection test, not admission: a surviving
/// route still takes the complete collision check before it can be drawn.
/// Stop once any limit is exceeded; otherwise all three returned counts are exact.
pub(super) fn route_crossings(
    d: &PlannedDimension,
    peers: &[&PlannedDimension],
    limits: [usize; 3],
) -> [usize; 3] {
    let Some(p) = d.placed.as_ref() else {
        return [0; 3];
    };
    let mut counts = [0; 3];
    for q in peers.iter().filter(|q| q.id != d.id) {
        let Some(r) = q.placed.as_ref() else {
            continue;
        };
        for (a, pa, b, pb) in [(d, p, *q, r), (*q, r, d, p)] {
            if a.kind != "callout" {
                continue;
            }
            let meets = |lines: &[[P2; 2]]| {
                pa.lines
                    .iter()
                    .any(|s| lines.iter().any(|t| super::leaders::lines_meet(*s, *t)))
            };
            let lines = meets(&pb.lines);
            let arrows = meets(&pb.arrows);
            counts[0] += usize::from(b.kind == "callout" && lines);
            counts[1] += usize::from(arrows);
            counts[2] += usize::from(lines || arrows);
            if (0..3).any(|k| counts[k] > limits[k]) {
                return counts;
            }
        }
    }
    counts
}

/// `place_linear_at` and `aligned_at_offset` reserve index 2 for the dimension
/// stroke. Any appended strokes are clipped extension continuations.
fn crossing_kind(kind: &str, index: usize) -> &'static str {
    match kind {
        "callout" => "leader",
        "linear" if index != 2 => "extension_line",
        "angular" if index < 2 => "extension_line",
        _ => "dimension_line",
    }
}

fn collisions_impl(
    analysis: &Analysis,
    dims: &[PlannedDimension],
    sheet: &PlannedSheet,
    views: &[PlannedView],
    cache: &super::footprints::Cache,
    only: Option<&str>,
) -> Vec<Problem> {
    let geometry = CollisionGeometry::new(analysis, dims, sheet, views, cache);
    collisions_prepared(analysis, dims, sheet, views, &geometry, only)
}

/// Immutable projection evidence shared by route trials. Only annotation ink and
/// reserved view areas may change while it is reused; paper and view frames stay fixed.
pub(super) struct CollisionGeometry<'a> {
    interior_proofs: BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
    opening_proofs: BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
    datum_proofs: BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
    outlines: Vec<(&'a str, [f64; 4])>,
    footprints: BTreeMap<&'a str, std::rc::Rc<super::footprints::Footprints>>,
}

impl<'a> CollisionGeometry<'a> {
    pub(super) fn new(
        analysis: &Analysis,
        dims: &[PlannedDimension],
        sheet: &PlannedSheet,
        views: &'a [PlannedView],
        cache: &super::footprints::Cache,
    ) -> Self {
        let interior_proofs = super::interior::proofs(analysis, views);
        let opening_proofs = if dims.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
        }) {
            super::interior::callout_proofs(analysis, views)
        } else {
            BTreeMap::new()
        };
        let datum_proofs = if dims.iter().any(|d| d.id.starts_with("pmi:datum.")) {
            super::interior::exterior_proofs(analysis, views)
        } else {
            BTreeMap::new()
        };
        let outlines: Vec<_> = outlines(analysis, views)
            .into_iter()
            .filter(|(id, _)| views.iter().any(|v| v.id == *id && v.sheet == sheet.number))
            .collect();
        let footprints: BTreeMap<_, _> = views
            .iter()
            .filter_map(|v| {
                let name = v.id.strip_prefix("v:")?;
                super::NAMES
                    .contains(&name)
                    .then(|| (v.id.as_str(), cache.get(analysis, v)))
            })
            .collect();
        Self {
            interior_proofs,
            opening_proofs,
            datum_proofs,
            outlines,
            footprints,
        }
    }

    pub(super) fn for_item(
        &self,
        analysis: &Analysis,
        dims: &[PlannedDimension],
        sheet: &PlannedSheet,
        views: &[PlannedView],
        id: &str,
    ) -> Vec<Problem> {
        collisions_prepared(analysis, dims, sheet, views, self, Some(id))
    }
}

fn collisions_prepared(
    analysis: &Analysis,
    dims: &[PlannedDimension],
    sheet: &PlannedSheet,
    views: &[PlannedView],
    geometry: &CollisionGeometry<'_>,
    only: Option<&str>,
) -> Vec<Problem> {
    let mut problems = Vec::new();
    let drawable = drawable(sheet);
    let CollisionGeometry {
        interior_proofs,
        opening_proofs,
        datum_proofs,
        outlines,
        footprints,
    } = geometry;
    let placed: Vec<(&PlannedDimension, &Placed)> = dims
        .iter()
        .filter(|d| {
            views
                .iter()
                .find(|v| v.id == d.view)
                .map_or(sheet.number == 1, |v| v.sheet == sheet.number)
        })
        .filter_map(|d| d.placed.as_ref().map(|p| (d, p)))
        .collect();
    let mut feature_problems = Vec::new();
    macro_rules! problem {
        ($code:expr, $severity:expr, $subjects:expr, $message:expr $(,)?) => {{
            let subjects: Vec<String> = $subjects;
            problems.push(Problem {
                id: format!("p:{}:{}", $code, subjects.join("|")),
                code: $code.into(),
                severity: $severity.into(),
                message: $message,
                subjects,
                ..Default::default()
            });
        }};
    }
    for (d, p) in &placed {
        if only.is_some_and(|id| d.id.as_str() != id) {
            continue;
        }
        if let Some(f) = footprints.get(d.view.as_str()) {
            let (code, shafts): (&str, &[[P2; 2]]) = match d.kind.as_str() {
                "callout" => {
                    // A datum's connected second segment is another shaft leg.
                    // A separate planar face extension is not a leader, and an
                    // ordinary callout's second segment remains its shelf.
                    let bent_datum = d.id.starts_with("pmi:datum.")
                        && p.lines.get(1).is_some_and(|s| p.lines[0][1] == s[0]);
                    (
                        "leader_through_feature",
                        &p.lines[..p.lines.len().min(if bent_datum { 2 } else { 1 })],
                    )
                }
                "linear" => (
                    "extension_through_feature",
                    &p.lines[..p.lines.len().min(2)],
                ),
                _ => ("", &[]),
            };
            for (n, shaft) in shafts.iter().enumerate() {
                let crossed = if d.kind == "callout" {
                    f.crossed(*shaft, &d.measurements)
                } else {
                    f.crossed_extension(*shaft, &d.measurements)
                };
                for feature in crossed {
                    let subjects = vec![d.id.clone(), feature];
                    feature_problems.push(Problem {
                        id: format!("p:{code}:{}:line:{n}", subjects.join("|")),
                        code: code.into(),
                        severity: "warning".into(),
                        message: format!(
                            "The {} of {} crosses {}.",
                            if d.kind == "callout" {
                                "leader"
                            } else if n == 0 {
                                "first extension line"
                            } else {
                                "second extension line"
                            },
                            named(d),
                            crate::wording::feature_id(analysis, &subjects[1])
                        ),
                        subjects,
                        ..Default::default()
                    });
                }
            }
        }
        // Its ink past its view's reserved area (the box and its bands).
        if let Some(v) = views.iter().find(|v| v.id == d.view) {
            let a = v.area;
            let past = ink_points(p)
                .map(|q| {
                    (a[0] - q[0])
                        .max(q[0] - a[2])
                        .max(a[1] - q[1])
                        .max(q[1] - a[3])
                })
                .fold(0.0, f64::max);
            if past > 1e-6 {
                problem!(
                    "annotation_outside_area",
                    "info",
                    vec![d.id.clone(), d.view.clone()],
                    format!(
                        "{} runs up to {past:.1} mm past the area reserved for {} and its bands; it may crowd what lies beyond.",
                        crate::wording::capital(&named(d)),
                        crate::wording::view(&d.view)
                    ),
                );
            }
        }
        // #32: a linear label stranded away from its own dimension line.
        if d.kind == "linear"
            && let Some(gap) = label_gap(&p.lines, p.label_box).filter(|g| *g > STRAY_TEXT)
        {
            problem!(
                "dimension_text_stray",
                "error",
                vec![d.id.clone()],
                format!(
                    "The text of {} lies {gap:.1} mm from its dimension line.",
                    named(d)
                ),
            );
        }
        let lb = p.label_box;
        let ends = p
            .lines
            .iter()
            .chain(&p.arrows)
            .flat_map(|s| s.iter().copied());
        // A table stands against the frame; annotations keep the frame gutter.
        let area = if d.id.starts_with("t:") {
            let [w, h] = sheet.page;
            [FRAME, FRAME, w - FRAME, h - FRAME]
        } else {
            drawable
        };
        if !inside([lb[0], lb[1]], area)
            || !inside([lb[2], lb[3]], area)
            || ends.clone().any(|e| !inside(e, area))
        {
            problem!(
                "annotation_off_sheet",
                "error",
                vec![d.id.clone()],
                format!(
                    "{} runs off the sheet's drawing area.",
                    crate::wording::capital(&named(d))
                ),
            );
        }
        let tb = sheet.title_block;
        if boxes_overlap(lb, tb) || p.lines.iter().any(|s| crosses(*s, tb)) {
            problem!(
                "annotation_over_title_block",
                "warning",
                vec![d.id.clone()],
                format!(
                    "{} runs into the title block.",
                    crate::wording::capital(&named(d))
                ),
            );
        }
        for (view, b) in outlines {
            if boxes_overlap(lb, *b)
                && !(*view == d.view
                    && views.iter().find(|v| v.id == d.view).is_some_and(|v| {
                        interior_proofs.get(&v.id).is_some_and(|proof| {
                            proof.blank(v, lb)
                                || (d.measurements.iter().any(|m| m.parameter == "notch.angle")
                                    && proof.exterior_annotation_clear(v, p))
                                || (d.measurements.iter().any(|m| {
                                    matches!(
                                        m.parameter.as_str(),
                                        super::profile_angles::PARAMETER | "notch.angle"
                                    )
                                }) && proof.exterior_clear(v, p.label_box, &[], &[]))
                        }) || (d
                            .measurements
                            .iter()
                            .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
                            && opening_proofs.get(&v.id).is_some_and(|proof| {
                                if d.kind == "linear" {
                                    proof.opening_linear_clear(v, p)
                                } else {
                                    proof.annotation_clear(v, p, true)
                                }
                            }))
                            || (d.id.starts_with("pmi:datum.")
                                && datum_proofs
                                    .get(&v.id)
                                    .is_some_and(|proof| proof.exterior_annotation_clear(v, p)))
                    }))
            {
                problem!(
                    "annotation_over_view",
                    "warning",
                    vec![d.id.clone(), view.to_string()],
                    format!(
                        "The label of {} lies over {}.",
                        named(d),
                        crate::wording::view(view)
                    ),
                );
            }
            // A line (a leader, an extension or dimension line) across a view not its own.
            if *view != d.view && p.lines.iter().any(|s| crosses(*s, *b)) {
                problem!(
                    "annotation_line_over_view",
                    "warning",
                    vec![d.id.clone(), view.to_string()],
                    format!(
                        "A line of {} runs across {}.",
                        named(d),
                        crate::wording::view(view)
                    ),
                );
            }
        }
    }
    for (i, (a, pa)) in placed.iter().enumerate() {
        for (b, pb) in &placed[i + 1..] {
            if only.is_some_and(|id| a.id.as_str() != id && b.id.as_str() != id) {
                continue;
            }
            // #32: two linear dimensions on one dimension line over overlapping spans (an
            // explicit lane the stack could not re-space): their arrows and extension lines run
            // into each other, whatever crossing the move cleared.
            if a.kind == "linear"
                && b.kind == "linear"
                && shared_dimension_line(&pa.lines, &pb.lines)
            {
                problem!(
                    "dimension_line_shared",
                    "error",
                    vec![a.id.clone(), b.id.clone()],
                    format!(
                        "{} and {} are drawn on one dimension line over overlapping spans.",
                        crate::wording::capital(&named(a)),
                        named(b)
                    ),
                );
            }
            // Only the dimension stroke crosses here; extension/extension contacts are allowed.
            // Both linear builders put the dimension stroke at index 2, including aligned
            // dimensions whose clipped extension continuations are appended after it.
            if a.kind == "linear"
                && b.kind == "linear"
                && pa
                    .lines
                    .get(2)
                    .zip(pb.lines.get(2))
                    .is_some_and(|(x, y)| super::leaders::segments_cross(*x, *y))
            {
                problem!(
                    "dimension_line_crossing",
                    "warning",
                    vec![a.id.clone(), b.id.clone()],
                    format!(
                        "The dimension lines of {} and {} cross.",
                        named(a),
                        named(b)
                    ),
                );
            }
            if boxes_overlap(pa.label_box, pb.label_box) {
                problem!(
                    "annotation_overlap",
                    "warning",
                    vec![a.id.clone(), b.id.clone()],
                    if pa.lines.iter().any(|s| crosses(*s, pb.label_box))
                        || pb.lines.iter().any(|s| crosses(*s, pa.label_box))
                        || (a.kind == "callout" && ink_through(pa, pb.label_box))
                        || (b.kind == "callout" && ink_through(pb, pa.label_box))
                    {
                        format!(
                            "The labels of {} and {} overlap; line-work also crosses a label, \
                             so separating the text is not enough: move what is drawn, not just the text.",
                            named(a),
                            named(b)
                        )
                    } else {
                        format!("The labels of {} and {} overlap.", named(a), named(b))
                    },
                );
            }
        }
    }
    for (a, pa) in &placed {
        for (b, pb) in &placed {
            if a.id == b.id || only.is_some_and(|id| a.id.as_str() != id && b.id.as_str() != id) {
                continue;
            }
            // A callout's leader is judged by its rendered ink, its arrowhead's flare included
            // (ISSUE-367-a), as `dims::leaders` places it; a contact only the flare makes is
            // worded as such. Linear dimension flares are the same filled ink.
            let through = pa.lines.iter().any(|s| crosses(*s, pb.label_box));
            // ISSUE-1332-a: an overlapping pair already has one finding above, including
            // the line-work remedy when needed. Do not charge that neighbourhood twice.
            if !boxes_overlap(pa.label_box, pb.label_box)
                && (through || ink_through(pa, pb.label_box))
            {
                problems.push(Problem {
                    id: format!("p:annotation_ink_overlap:{}|{}", a.id, b.id),
                    code: "annotation_ink_overlap".into(),
                    severity: "warning".into(),
                    subjects: vec![a.id.clone(), b.id.clone()],
                    ink_is_leader: Some(a.kind == "callout" && through),
                    message: if through {
                        format!(
                            "A line of {} runs through the label of {}.",
                            named(a),
                            named(b)
                        )
                    } else {
                        format!(
                            "The arrowhead of {} reaches into the label of {}.",
                            named(a),
                            named(b)
                        )
                    },
                    ..Default::default()
                });
            }
            // Keep one finding per ordered pair, with every kind actually met. Classification
            // runs only after the original short-circuit contact predicate succeeds.
            if a.kind == "callout"
                && pa.lines.iter().any(|s| {
                    pb.lines
                        .iter()
                        .chain(&pb.arrows)
                        .any(|t| super::leaders::lines_meet(*s, *t))
                })
            {
                let mut kinds = Vec::new();
                for kind in ["leader", "dimension_line", "extension_line", "arrow"] {
                    let met = if kind == "arrow" {
                        pb.arrows
                            .iter()
                            .any(|t| pa.lines.iter().any(|s| super::leaders::lines_meet(*s, *t)))
                    } else {
                        pb.lines.iter().enumerate().any(|(i, t)| {
                            crossing_kind(&b.kind, i) == kind
                                && pa.lines.iter().any(|s| super::leaders::lines_meet(*s, *t))
                        })
                    };
                    if met {
                        kinds.push(kind.into());
                    }
                }
                problems.push(Problem {
                    id: format!("p:annotation_leader_crossing:{}|{}", a.id, b.id),
                    code: "annotation_leader_crossing".into(),
                    severity: "warning".into(),
                    subjects: vec![a.id.clone(), b.id.clone()],
                    message: format!("The leader of {} crosses a line of {}.", named(a), named(b)),
                    crossing_kinds: kinds,
                    ..Default::default()
                });
            }
        }
    }
    problems.extend(feature_problems);
    problems
}

#[cfg(test)]
mod tests {
    #[test]
    fn inclined_spans_choose_the_nearer_parallel_support() {
        let outline = [0., 0., 100., 100.];
        assert_eq!(
            aligned_strip([78., 70.], [85., 66.], outline, Side::Below),
            Side::Above
        );
        assert_eq!(
            aligned_strip([85., 66.], [78., 70.], outline, Side::Below),
            Side::Above
        );
        assert_eq!(
            aligned_strip([22., 30.], [15., 34.], outline, Side::Above),
            Side::Below
        );
    }

    #[test]
    fn equal_source_spans_follow_geometry_in_either_entity_order() {
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,100]], "iso_bounds":[0,0,100,100]
        }))
        .unwrap();
        let frames = BTreeMap::from([(
            "front",
            Frame::looking(&a, [0., -1., 0.], [0., 0., 1.], [0., 0.], 1.),
        )]);
        let want = |id: &str, lo| Want {
            id: format!("d:pmi:{id}"),
            views: vec!["front"],
            value: 10.,
            terms: vec!["10".into(), "±0.2".into()],
            measurements: vec![],
            basis: String::new(),
            turning: None,
            shape: Shape::Linear {
                a: [50., 0., lo],
                b: [50., 0., lo + 10.],
                axis: 2,
                a_edge: false,
                b_edge: false,
                order: 1,
            },
        };
        for items in [
            [want("upper", 70.), want("lower", 20.)],
            [want("lower", 20.), want("upper", 70.)],
        ] {
            let (_, lanes) = lanes(
                &a,
                &frames,
                items.iter().map(|w| (w, Some("front"))),
                &[None; 2],
            );
            for (w, lane) in items.iter().zip(lanes) {
                assert_eq!(lane, usize::from(w.id.ends_with("upper")), "{}", w.id);
            }
        }
    }

    #[test]
    fn reused_route_geometry_rechecks_moved_ink_and_grown_areas() {
        let analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
        }))
        .unwrap();
        let plan = crate::model::plan(&analysis, &crate::model::Spec::default()).unwrap();
        let cache = super::super::footprints::Cache::default();
        let geometry = CollisionGeometry::new(
            &analysis,
            &plan.dimensions,
            &plan.sheet,
            &plan.views,
            &cache,
        );
        let mut dims = plan.dimensions.clone();
        let id = dims[0].id.clone();
        for dx in [0., 15.] {
            let p = dims[0].placed.as_mut().unwrap();
            p.label_box[2] += dx;
            for pad in [-5., 5.] {
                let mut views = plan.views.clone();
                let v = views.iter_mut().find(|v| v.id == dims[0].view).unwrap();
                let b = super::super::ink_box(&dims[0]).unwrap();
                v.area = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
                let fresh = collisions_for(&analysis, &dims, &plan.sheet, &views, &cache, &id);
                assert_eq!(
                    geometry.for_item(&analysis, &dims, &plan.sheet, &views, &id),
                    fresh
                );
                assert_eq!(
                    fresh.iter().any(|p| p.code == "annotation_outside_area"),
                    pad < 0.
                );
            }
        }
    }

    #[test]
    fn crossing_kinds_and_dimension_strokes_follow_the_placed_structure() {
        let analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
        }))
        .unwrap();
        let plan = crate::model::plan(&analysis, &crate::model::Spec::default()).unwrap();
        let item = |id: &str, kind: &str, lines, arrows, label_box| {
            let mut d = plan.dimensions[0].clone();
            d.id = id.into();
            d.kind = kind.into();
            d.placed = Some(Placed {
                lines,
                arrows,
                label_box,
                label_at: [label_box[0], label_box[1]],
                rotation: 0.,
            });
            d
        };
        let horizontal = [[30., 80.], [70., 80.]];
        let vertical = [[50., 60.], [50., 100.]];
        let far = [[130., 130.], [140., 130.]];
        let leader = item(
            "d:leader",
            "callout",
            vec![horizontal],
            vec![],
            [80., 110., 90., 113.],
        );
        let mut peer = item(
            "d:peer",
            "linear",
            vec![far, far, far],
            vec![],
            [100., 110., 110., 113.],
        );
        let kinds = |a: &PlannedDimension, b: &PlannedDimension| {
            collisions(&analysis, &[a.clone(), b.clone()], &plan.sheet, &[])
                .into_iter()
                .find(|p| p.code == "annotation_leader_crossing")
                .unwrap()
        };
        for (index, kind) in [
            (0, "extension_line"),
            (1, "extension_line"),
            (2, "dimension_line"),
            (3, "extension_line"),
        ] {
            peer.placed.as_mut().unwrap().lines = vec![far; 4];
            peer.placed.as_mut().unwrap().lines[index] = vertical;
            let p = kinds(&leader, &peer);
            assert_eq!(p.crossing_kinds, [kind]);
            assert_eq!(p.subjects, ["d:leader", "d:peer"]);
            assert_eq!(p.id, "p:annotation_leader_crossing:d:leader|d:peer");
            let json = serde_json::to_value(&p).unwrap();
            assert_eq!(serde_json::from_value::<Problem>(json).unwrap(), p);
        }
        peer.kind = "callout".into();
        assert_eq!(kinds(&leader, &peer).crossing_kinds, ["leader"]);
        peer.kind = "linear".into();
        peer.placed.as_mut().unwrap().lines = vec![vertical, far, vertical];
        peer.placed.as_mut().unwrap().arrows = vec![vertical];
        assert_eq!(
            kinds(&leader, &peer).crossing_kinds,
            ["dimension_line", "extension_line", "arrow"]
        );
        peer.placed.as_mut().unwrap().lines = vec![far; 3];
        assert_eq!(kinds(&leader, &peer).crossing_kinds, ["arrow"]);

        // The rim-search rejection must count both ordered pairs, all shaft
        // legs and arrow-only contacts exactly as the complete checker does.
        for ak in ["callout", "linear", "angular"] {
            for bk in ["callout", "linear", "angular"] {
                for shift in [0., 20., 50.] {
                    let mut a = leader.clone();
                    a.kind = ak.into();
                    a.placed.as_mut().unwrap().arrows = vec![horizontal];
                    let mut b = peer.clone();
                    b.kind = bk.into();
                    let stroke = vertical.map(|p| [p[0] + shift, p[1]]);
                    b.placed.as_mut().unwrap().lines = vec![stroke; 4];
                    b.placed.as_mut().unwrap().arrows = vec![stroke];
                    let problems = collisions(&analysis, &[a.clone(), b.clone()], &plan.sheet, &[]);
                    let expected = ["leader", "arrow", "total"].map(|kind| {
                        problems
                            .iter()
                            .filter(|p| {
                                p.code == "annotation_leader_crossing"
                                    && (kind == "total"
                                        || p.crossing_kinds.iter().any(|k| k == kind))
                            })
                            .count()
                    });
                    for d in [&a, &b] {
                        assert_eq!(route_crossings(d, &[&a, &b], [usize::MAX; 3]), expected);
                        for limit in 0..=2 {
                            let counts = route_crossings(d, &[&a, &b], [limit; 3]);
                            assert_eq!(
                                counts.iter().any(|&n| n > limit),
                                expected.iter().any(|&n| n > limit)
                            );
                            if counts.iter().all(|&n| n <= limit) {
                                assert_eq!(counts, expected);
                            }
                        }
                    }
                }
            }
        }

        // The leader through a label is distinct from a linear line or a flare-only hit.
        peer.placed.as_mut().unwrap().label_box = [45., 79., 55., 81.];
        for (kind, expected) in [("callout", true), ("linear", false)] {
            let mut a = leader.clone();
            a.kind = kind.into();
            let p = collisions(&analysis, &[a, peer.clone()], &plan.sheet, &[])
                .into_iter()
                .find(|p| p.code == "annotation_ink_overlap")
                .unwrap();
            assert_eq!(p.ink_is_leader, Some(expected));
        }

        let mut a = item(
            "d:a",
            "linear",
            vec![far, far, horizontal],
            vec![],
            [80., 110., 90., 113.],
        );
        let b = item(
            "d:b",
            "linear",
            vec![horizontal, horizontal, vertical],
            vec![],
            [100., 110., 110., 113.],
        );
        let findings = |a: &PlannedDimension, b: &PlannedDimension| {
            collisions(&analysis, &[a.clone(), b.clone()], &plan.sheet, &[])
                .into_iter()
                .filter(|p| p.code == "dimension_line_crossing")
                .collect::<Vec<_>>()
        };
        let ps = findings(&a, &b);
        assert_eq!(ps.len(), 1);
        assert_eq!(ps[0].severity, "warning");
        assert_eq!(ps[0].subjects, ["d:a", "d:b"]);
        a.placed.as_mut().unwrap().lines = vec![vertical, vertical, far];
        assert!(findings(&a, &b).is_empty(), "extensions may cross");
        a.placed.as_mut().unwrap().lines[2] = [[30., 60.], [50., 60.]];
        assert!(findings(&a, &b).is_empty(), "endpoint touches are allowed");
    }

    #[test]
    fn item_collision_checks_preserve_all_findings_involving_that_item() {
        let analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
        }))
        .unwrap();
        let plan = crate::model::plan(&analysis, &crate::model::Spec::default()).unwrap();
        let mut dimensions = plan.dimensions.clone();
        let mut changed = 0;
        for d in dimensions.iter_mut().filter(|d| d.placed.is_some()).take(2) {
            let p = d.placed.as_mut().unwrap();
            p.label_box = [-3., -3., 3., 3.];
            p.label_at = [0., 0.];
            changed += 1;
        }
        assert_eq!(changed, 2);
        let cache = super::super::footprints::Cache::default();
        let complete = collisions_cached(&analysis, &dimensions, &plan.sheet, &plan.views, &cache);
        assert!(complete.iter().any(|p| p.code == "annotation_overlap"));
        assert!(complete.iter().any(|p| p.code == "annotation_off_sheet"));
        for id in dimensions.iter().map(|d| d.id.as_str()).chain(["absent"]) {
            let expected: Vec<_> = complete
                .iter()
                .filter(|p| p.subjects.iter().any(|s| s == id))
                .cloned()
                .collect();
            assert_eq!(
                collisions_for(&analysis, &dimensions, &plan.sheet, &plan.views, &cache, id),
                expected,
                "{id}"
            );
        }
    }

    #[test]
    fn indexed_peer_ink_matches_the_complete_linear_collision_predicate() {
        for peer_arrows in 0..=2 {
            for allow_transverse in [false, true] {
                for shift in -12..=12 {
                    let mut a = Placed {
                        lines: vec![
                            [[0., 0.], [0., 10.]],
                            [[10., 0.], [10., 10.]],
                            [[0., 10.], [10., 10.]],
                        ],
                        arrows: vec![arrow([0., 10.], [1., 10.]), arrow([10., 10.], [9., 10.])],
                        label_at: [0., 12.],
                        rotation: 0.,
                        label_box: [0., 11., 8., 14.],
                    };
                    let skew = f64::from(shift) * 0.3;
                    for line in a.lines.iter_mut().take(2) {
                        line[1][0] += skew;
                    }
                    for p in a.lines.iter_mut().skip(2).chain(&mut a.arrows).flatten() {
                        p[0] += skew;
                    }
                    a.label_box[0] += skew;
                    a.label_box[2] += skew;
                    a.label_at[0] += skew;
                    let mut b = a.clone();
                    for p in b.lines.iter_mut().chain(&mut b.arrows).flatten() {
                        p[0] += f64::from(shift);
                        p[1] += 3.;
                    }
                    b.label_box = [f64::from(shift), 14., f64::from(shift) + 8., 17.];
                    b.arrows.truncate(peer_arrows);
                    let indexed = PeerInk::new(std::iter::once(&b));
                    let mut scratch = a.clone();
                    for x in -20..=20 {
                        let mut moved = a.clone();
                        moved.label_box[0] += f64::from(x);
                        moved.label_box[2] += f64::from(x);
                        moved.lines[2][0][0] = moved.lines[2][0][0].min(moved.label_box[0]);
                        moved.lines[2][1][0] = moved.lines[2][1][0].max(moved.label_box[2]);
                        LinearStroke::new(&moved).write(&mut scratch);
                        assert_eq!(
                            serde_json::to_vec(&scratch).unwrap(),
                            serde_json::to_vec(&moved).unwrap()
                        );
                        assert_eq!(
                            WitnessPeers::new(std::iter::once(&b))
                                .contact(&scratch, |_| allow_transverse),
                            linear_witness_contact(&moved, &b, allow_transverse)
                        );
                        assert_eq!(
                            linear_pair_contact(&moved, &b, allow_transverse),
                            linear_witness_contact(&moved, &b, allow_transverse)
                                || indexed.mutable_contact(&moved)
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn repeated_linear_label_bounds_match_the_full_standard_font_ink() {
        for terms in [
            vec!["21.0"],
            vec!["⌀10 H7", "+0.015\n0"],
            vec!["4×", "R3.94"],
            vec!["(25.0)"],
            vec!["WIDTH", "12 ±0.1"],
        ] {
            let terms: Vec<_> = terms.into_iter().map(str::to_owned).collect();
            let full = label_ink(&terms, font());
            for _ in 0..2 {
                assert_eq!(linear_label_bounds(&terms), (full.min, full.max));
            }
        }
    }
    /// ARCH0009-5: deliberately scramble input order and make the size longer than a
    /// location: category precedes distance; datum locations alone nest by distance.
    #[test]
    fn shared_corridor_orders_sizes_then_locations_then_overall() {
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding": {"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
        }))
        .unwrap();
        let frames = BTreeMap::from([(
            "front",
            Frame::looking(&a, [0., -1., 0.], [0., 0., 1.], [0., 0.], 1.),
        )]);
        let want = |id: &str, span: f64, order| Want {
            id: id.into(),
            views: vec!["front"],
            value: span,
            terms: vec![span.to_string()],
            measurements: vec![],
            basis: String::new(),
            turning: None,
            shape: Shape::Linear {
                a: [0., 0., 0.],
                b: [span, 0., 0.],
                axis: 0,
                a_edge: false,
                b_edge: false,
                order,
            },
        };
        let items = [
            want("overall", 100., 2),
            want("far", 70., 1),
            want("size", 40., 0),
            want("near", 20., 1),
            want("middle", 50., 1),
        ];
        let (sides, lane) = lanes(
            &a,
            &frames,
            items.iter().map(|w| (w, Some("front"))),
            &[None; 5],
        );
        assert!(sides.iter().all(|s| *s == Some(Side::Below)));
        assert_eq!(lane, [4, 3, 0, 1, 2]);
    }

    #[test]
    fn shared_lines_counts_overlapping_spans_on_one_dimension_line() {
        // FTC-08's 49.124 and 64.999: two vertical locations from one datum, one lane.
        let vertical = |x: f64, y0: f64, y1: f64| Placed {
            lines: vec![
                [[x - 20., y0], [x + 1., y0]],
                [[x - 20., y1], [x + 1., y1]],
                [[x, y0], [x, y1]],
            ],
            arrows: vec![[[x, y0], [x, y0 + 2.7]], [[x, y1], [x, y1 - 2.7]]],
            label_at: [x - 1., (y0 + y1) / 2.],
            rotation: 90.,
            label_box: [x - 3., y0 + 2., x - 1., y0 + 8.],
        };
        let a = vertical(236.58, 197.27, 213.5);
        let b = vertical(236.58, 197.27, 216.67);
        let apart = vertical(243.58, 197.27, 216.67);
        assert_eq!(shared_lines(&[Some(a.clone()), Some(b.clone())]), 1);
        assert_eq!(shared_lines(&[Some(a.clone()), None, Some(apart)]), 0);
        // A chain meeting at one witness shares no span.
        let next = vertical(236.58, 213.5, 230.);
        assert_eq!(shared_lines(&[Some(a), Some(next)]), 0);
    }

    #[test]
    fn a_near_face_start_moves_only_the_witness_starts_beyond_the_body() {
        // FTC-08's 141.827 slot location: a vertical dimension left of the plan view, whose
        // second witness starts at the slot end inside the body (outline x 60..240).
        let c = LinearStroke {
            lines: [
                [[58., 10.], [20., 10.]],
                [[220., 80.], [20., 80.]],
                [[22., 10.], [22., 80.]],
            ],
            arrows: [[[22., 10.], [22., 12.7]], [[22., 80.], [22., 77.3]]],
            label_at: [21., 45.],
            rotation: 90.,
            label_box: [19., 40., 21., 50.],
        };
        let outline = [60., 0., 240., 100.];
        let near = near_face_start(&c, outline, Side::Left).unwrap();
        assert_eq!(
            near.lines[0], c.lines[0],
            "a start already beyond the face stays"
        );
        assert_eq!(near.lines[1][0], [60. - GAP, 80.]);
        assert_eq!(near.lines[1][1], c.lines[1][1]);
        assert_eq!(near.lines[2], c.lines[2]);
        assert_eq!(near.arrows, c.arrows);
        assert_eq!(near.label_box, c.label_box);
        // A witness that would no longer leave the face by a millimetre is no stroke.
        let mut short = c;
        short.lines[1][1][0] = 60. - GAP - 0.5;
        assert!(near_face_start(&short, outline, Side::Left).is_none());
    }

    #[test]
    fn oblique_arrow_clearance_uses_the_actual_witness_corridor() {
        let witness = [[0., 0.], [10., 10.]];
        assert!(!touches_witness([[1., 8.], [2., 8.]], witness, 0.2));
        assert!(touches_witness([[4., 4.1], [6., 6.1]], witness, 0.2));
        assert!(touches_witness([[10., 10.1], [10.1, 10.1]], witness, 0.2));
        assert!(!touches_witness([[4., 4.4], [6., 6.4]], witness, 0.2));
    }

    #[test]
    fn automatic_chains_share_lanes_in_both_page_directions() {
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding": {"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,100]], "iso_bounds":[0,0,100,100]
        }))
        .unwrap();
        let frames = BTreeMap::from([(
            "front",
            Frame::looking(&a, [0., -1., 0.], [0., 0., 1.], [0., 0.], 1.),
        )]);
        for axis in [0, 2] {
            let want = |lo: f64, hi: f64, text: &str| {
                let (mut a, mut b) = ([0.; 3], [0.; 3]);
                a[axis] = lo;
                b[axis] = hi;
                Want {
                    id: format!("{lo}-{hi}"),
                    views: vec!["front"],
                    value: (hi - lo).abs(),
                    terms: vec![text.into()],
                    measurements: vec![],
                    basis: String::new(),
                    turning: None,
                    shape: Shape::Linear {
                        a,
                        b,
                        axis,
                        a_edge: false,
                        b_edge: false,
                        order: 1,
                    },
                }
            };
            // The middle link arrives last and must join both halves. Reversed endpoints
            // are equivalent; overlapping baselines and a short, outside-text link are not.
            let items = [
                want(0., 20., "20"),
                want(80., 50., "30"),
                want(20., 50., "30"),
                want(0., 80., "80"),
                want(80., 82., "2"),
            ];
            let (_, lane) = lanes(
                &a,
                &frames,
                items.iter().map(|w| (w, Some("front"))),
                &[None; 5],
            );
            assert_eq!(lane[0], lane[1]);
            assert_eq!(lane[0], lane[2]);
            assert_ne!(lane[0], lane[3]);
            assert_ne!(lane[0], lane[4]);
            assert_eq!(
                chain_lanes(&frames["front"], &items.iter().collect::<Vec<_>>()),
                [0, 0, 0, 1, 2]
            );
        }
    }

    #[test]
    fn front_left_lanes_do_not_reserve_the_plan_band() {
        let front = Some("front");
        let bands = super::band_lanes([((&front, Some(super::Side::Left)), 2)].into_iter());
        assert_eq!(bands.plate_front_left, 3);
        assert_eq!(bands.plan_left, 0);
    }

    use super::*;

    /// `slides` stays bounded by the sheet however long the dimension line: a 200 km line at 1:1
    /// (`tests/views.rs`'s fallback part) gives no more places than the drawing area has steps,
    /// each with the label on it, and the nearest first.
    #[test]
    fn slides_are_bounded_by_the_sheet() {
        let drawable = [10.0, 10.0, 584.0, 410.0];
        let (y, len) = (50.0, 2.0e8);
        let p = Placed {
            lines: vec![
                [[0.0, 40.0], [0.0, 52.0]],
                [[len, 40.0], [len, 52.0]],
                [[0.0, y], [len, y]],
            ],
            arrows: vec![],
            label_at: [100.0, y + LIFT],
            rotation: 0.0,
            label_box: [100.0, y + LIFT, 110.0, y + LIFT + CAP],
        };
        let bound = (2.0 * (drawable[2] - drawable[0]) / SLIDE_STEP) as usize + 4;
        let places: Vec<Placed> = slides(&p, drawable).take(bound + 1).collect();
        assert!(
            !places.is_empty() && places.len() <= bound,
            "{}",
            places.len()
        );
        assert!(
            places
                .iter()
                .all(|c| c.label_box[0] >= drawable[0] && c.label_box[2] <= drawable[2])
        );
        assert_eq!(places[0].label_box[0], 100.0 + SLIDE_STEP);
    }

    /// ISSUE-367-a, planted (the drawings of the parts here no longer have a case since callout
    /// leaders are placed clear of labels, stream d-callout-leaders): an item whose arrowhead
    /// reaches into another item's label, its shaft through the label, is reported as
    /// `annotation_ink_overlap` naming both; and a callout's leader across another item's line is
    /// `annotation_leader_crossing`.
    #[test]
    fn an_arrowhead_and_a_leader_through_other_ink_are_reported() {
        let analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema": "draftwright.analysis/5",
            "binding": {"source_sha256": "", "part": "", "face_count": 0, "loader": ""},
            "envelope": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            "iso_bounds": [0.0, 0.0, 0.0, 0.0]
        }))
        .unwrap();
        let sheet: PlannedSheet = serde_json::from_value(serde_json::json!({
            "size": "A4", "page": [297.0, 210.0], "scale": "1:1", "scale_factor": 1.0,
            "arrangement": "columns", "title_block": [200.0, 10.0, 287.0, 40.0],
            "default": true, "basis": "", "state": { "kind": "shown" }, "title": {}
        }))
        .unwrap();
        let item = |id: &str, kind: &str, placed: Placed| PlannedDimension {
            callout_offset: None,
            reference: false,
            determined_by: Vec::new(),
            region: None,
            id: id.into(),
            kind: kind.into(),
            view: "v:plan".into(),
            value: 1.0,
            label: id.into(),
            terms: vec![id.into()],
            measurements: vec![],
            default: true,
            from_spec: vec![],
            basis: String::new(),
            state: crate::items::State::Shown,
            placed: Some(placed),
        };
        // A dimension line ending in an arrowhead whose tip lies in the label at x 50..60.
        let dim = item(
            "d:a",
            "linear",
            Placed {
                lines: vec![[[30.0, 100.0], [55.0, 100.0]]],
                arrows: vec![[[55.0, 100.0], [52.3, 100.0]]],
                label_at: [35.0, 101.0],
                rotation: 0.0,
                label_box: [35.0, 101.0, 45.0, 103.0],
            },
        );
        let label = item(
            "d:b",
            "linear",
            Placed {
                lines: vec![],
                arrows: vec![],
                label_at: [50.0, 99.0],
                rotation: 0.0,
                label_box: [50.0, 99.0, 60.0, 101.5],
            },
        );
        // A leader up across the dimension line at x 40.
        let callout = item(
            "d:c",
            "callout",
            Placed {
                lines: vec![
                    [[40.0, 90.0], [42.0, 110.0]],
                    [[42.0, 110.0], [45.0, 110.0]],
                ],
                arrows: vec![[[40.0, 90.0], [40.3, 92.7]]],
                label_at: [46.0, 109.0],
                rotation: 0.0,
                label_box: [46.0, 109.0, 56.0, 111.5],
            },
        );
        let problems = collisions(&analysis, &[dim, label, callout], &sheet, &[]);
        let has = |code: &str, a: &str, b: &str| {
            problems
                .iter()
                .any(|p| p.code == code && p.subjects == [a.to_string(), b.to_string()])
        };
        assert!(has("annotation_ink_overlap", "d:a", "d:b"), "{problems:#?}");
        assert!(
            has("annotation_leader_crossing", "d:c", "d:a"),
            "{problems:#?}"
        );
        assert!(!has("annotation_leader_crossing", "d:a", "d:c"));
    }
    /// ISSUE-367-a: a callout's collision checks read its rendered ink, not only the shaft. An
    /// arrowhead whose flare (not its shaft) reaches into another label is reported; the flare
    /// is evidence only at the tip (a label beside the shaft further up is not met).
    #[test]
    fn a_callouts_arrowhead_flare_is_collision_evidence() {
        let leader = |tip: P2, elbow: P2| Placed {
            lines: vec![[tip, elbow], [elbow, [elbow[0] + 10.0, elbow[1]]]],
            arrows: vec![arrow(tip, elbow)],
            label_at: [elbow[0] + 1.0, elbow[1] + 0.5],
            rotation: 0.0,
            label_box: [
                elbow[0] + 1.0,
                elbow[1] + 0.5,
                elbow[0] + 9.0,
                elbow[1] + 3.0,
            ],
        };
        let other = |b: [f64; 4]| Placed {
            lines: vec![],
            arrows: vec![],
            label_at: [b[0], b[1]],
            rotation: 0.0,
            label_box: b,
        };
        // A vertical leader up from (50, 50): its flare is 0.45 mm either side at the tail.
        let up = leader([50.0, 50.0], [50.0, 80.0]);
        // A label whose left edge is 0.3 mm right of the shaft, beside the arrowhead.
        assert!(ink_through(&up, [50.3, 51.0, 60.0, 53.0]));
        assert!(
            !up.lines
                .iter()
                .any(|s| crosses(*s, [50.3, 51.0, 60.0, 53.0]))
        );
        // The same label further up the shaft, past the arrowhead: clear.
        assert!(!ink_through(&up, [50.3, 60.0, 60.0, 62.0]));
        // A tiny label wholly inside the flare.
        assert!(ink_through(&up, [49.9, 52.0, 50.1, 52.2]));
        // Reported by the plan's collision checks.
        let analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema": "draftwright.analysis/5",
            "binding": {"source_sha256": "", "part": "", "face_count": 0, "loader": ""},
            "envelope": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            "iso_bounds": [0.0, 0.0, 0.0, 0.0]
        }))
        .unwrap();
        let sheet: PlannedSheet = serde_json::from_value(serde_json::json!({
            "size": "A4", "page": [297.0, 210.0], "scale": "1:1", "scale_factor": 1.0,
            "arrangement": "columns", "title_block": [200.0, 10.0, 287.0, 40.0],
            "default": true, "basis": "", "state": { "kind": "shown" }, "title": {}
        }))
        .unwrap();
        let item = |id: &str, kind: &str, placed: Placed| PlannedDimension {
            callout_offset: None,
            reference: false,
            determined_by: Vec::new(),
            region: None,
            id: id.into(),
            kind: kind.into(),
            view: "v:plan".into(),
            value: 1.0,
            label: id.into(),
            terms: vec![id.into()],
            measurements: vec![],
            default: true,
            from_spec: vec![],
            basis: String::new(),
            state: crate::items::State::Shown,
            placed: Some(placed),
        };
        let problems = collisions(
            &analysis,
            &[
                item("d:c", "callout", up),
                item("d:l", "linear", other([50.3, 51.0, 60.0, 53.0])),
            ],
            &sheet,
            &[],
        );
        let overlap = problems
            .iter()
            .find(|p| p.code == "annotation_ink_overlap")
            .expect("the flare's contact is reported");
        assert_eq!(overlap.subjects, ["d:c", "d:l"]);
        assert_eq!(overlap.ink_is_leader, Some(false));
        assert!(
            overlap.message.starts_with("The arrowhead of"),
            "{}",
            overlap.message
        );
    }

    /// ISSUE-1332-a: the exact overlapping/crossing neighbourhood from Python's
    /// test_issue_1332_overlap_remedy.py, in either inventory order. Moving text alone cannot
    /// clear the line-work; an ordinary overlap keeps the ordinary message.
    #[test]
    fn an_overlapping_pair_has_one_diagnostic_with_a_consistent_remedy() {
        use serde_json::json;
        let analysis: Analysis = serde_json::from_value(json!({
            "schema": "draftwright.analysis/5",
            "binding": {"source_sha256": "", "part": "", "face_count": 0, "loader": ""},
            "envelope": [[0., 0., 0.], [1., 1., 1.]], "iso_bounds": [0., 0., 0., 0.]
        }))
        .unwrap();
        let sheet: PlannedSheet = serde_json::from_value(json!({
            "size": "A4", "page": [297., 210.], "scale": "1:1", "scale_factor": 1.,
            "arrangement": "columns", "title_block": [200., 10., 287., 40.],
            "default": true, "basis": "", "state": {"kind": "shown"}, "title": {}
        }))
        .unwrap();
        let item = |id: &str, label_box: [f64; 4], lines: Vec<[P2; 2]>| PlannedDimension {
            callout_offset: None,
            id: id.into(),
            kind: "linear".into(),
            view: "v:plan".into(),
            value: 1.,
            label: id.into(),
            terms: vec![id.into()],
            measurements: vec![],
            default: true,
            from_spec: vec![],
            basis: String::new(),
            state: crate::items::State::Shown,
            reference: false,
            determined_by: vec![],
            region: None,
            placed: Some(Placed {
                lines,
                arrows: vec![],
                label_at: [label_box[0], label_box[1]],
                rotation: 0.,
                label_box,
            }),
        };
        for crossing in [false, true] {
            let a = item(
                "d:a",
                [10., 10., 20., 12.2],
                vec![[
                    [0., if crossing { 11. } else { 40. }],
                    [40., if crossing { 11. } else { 40. }],
                ]],
            );
            let b = item(
                "d:b",
                [12., 10.5, 22., 12.7],
                vec![[[30., 30.], [31., 30.]]],
            );
            for items in [vec![a.clone(), b.clone()], vec![b.clone(), a.clone()]] {
                let found: Vec<_> = collisions(&analysis, &items, &sheet, &[])
                    .into_iter()
                    .filter(|p| {
                        matches!(
                            p.code.as_str(),
                            "annotation_overlap" | "annotation_ink_overlap"
                        )
                    })
                    .collect();
                assert_eq!(found.len(), 1, "{found:?}");
                assert_eq!(found[0].code, "annotation_overlap");
                assert_eq!(found[0].message.contains("move what is drawn"), crossing);
                assert_eq!(
                    found[0]
                        .message
                        .contains("separating the text is not enough"),
                    crossing
                );
                assert!(
                    found[0].fix.is_empty(),
                    "no unsupported edit is offered as a remedy"
                );
            }
        }
    }
}
