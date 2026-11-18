//! Bounded label alternatives on settled ink. GD&T and callout routes are immutable obstacles;
//! moving a dimension may remove contact, but may never add it (ISSUE-1334-a).
use super::placement::{Page, boxes_overlap, crosses, ink_points};
use super::{ARROW, Placed, PlannedDimension, lane_pitch};
use crate::model::{Analysis, PlannedSheet, PlannedView, Spec};

const CLEAR: f64 = 1.0;
const OFF_SPAN: f64 = ARROW + 3.0;

fn axis(p: &Placed) -> usize {
    usize::from(p.rotation != 0.0)
}

fn tips(p: &Placed) -> (f64, f64) {
    let k = axis(p);
    let (a, b) = (p.arrows[0][0][k], p.arrows[1][0][k]);
    (a.min(b), a.max(b))
}

pub(super) fn off_span<'a>(p: &Placed, others: impl Iterator<Item = &'a Placed>) -> bool {
    if p.arrows.len() != 2 {
        return false;
    }
    let k = axis(p);
    let (lo, hi) = tips(p);
    let b = p.label_box;
    if b[k] >= lo - 1e-6 && b[k + 2] <= hi + 1e-6 {
        return false;
    }
    if (lo - b[k + 2]).max(b[k] - hi) >= OFF_SPAN - 1e-6 {
        return true;
    }
    let t = p.arrows[0][0][1 - k];
    let normal = [t.min(b[1 - k]), t.max(b[3 - k])];
    others.filter(|q| q.arrows.len() == 2).any(|q| {
        q.lines
            .iter()
            .enumerate()
            .filter(|(i, _)| *i != 2)
            .any(|(_, [a, z])| {
                let between = (b[k] + 1e-6 < a[k] && a[k] < lo - 1e-6)
                    || (hi + 1e-6 < a[k] && a[k] < b[k + 2] - 1e-6);
                (a[k] - z[k]).abs() <= 1e-6
                    && between
                    && a[1 - k].min(z[1 - k]) <= normal[1]
                    && a[1 - k].max(z[1 - k]) >= normal[0]
            })
    })
}

fn ink_bounds(p: &Placed) -> [f64; 4] {
    ink_points(p).fold(
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
    )
}

// Two millimetres conservatively exceed every contact and clearance margin
// below. A separated pair needs none of the exact stroke/head predicates.
fn contact_bounds(p: &Placed) -> [f64; 4] {
    let b = ink_bounds(p);
    [b[0] - 2., b[1] - 2., b[2] + 2., b[3] + 2.]
}

fn shared_span_contact(p: &Placed, q: &PlannedDimension) -> bool {
    let Some(qp) = &q.placed else { return false };
    if q.kind == "linear"
        && p.arrows.len() == 2
        && qp.arrows.len() == 2
        && p.rotation == qp.rotation
    {
        let k = axis(p);
        let (a, b) = tips(p);
        let (c, d) = tips(qp);
        if (p.arrows[0][0][1 - k] - qp.arrows[0][0][1 - k]).abs() < 1e-6
            && b.min(d) - a.max(c) > 1e-6
        {
            return true;
        }
    }

    false
}

/// Finished label/leader predicates, plus the measured-span guard for sharing a lane.
fn contacts(p: &Placed, q: &PlannedDimension) -> u8 {
    let Some(qp) = &q.placed else {
        return 0;
    };
    let mut hits = 0;
    if shared_span_contact(p, q) {
        hits |= 1 << 4;
    }

    if boxes_overlap(p.label_box, qp.label_box) {
        hits |= 1 << 0;
    }
    if p.lines
        .iter()
        .copied()
        .chain(p.arrows.iter().flat_map(|h| super::leaders::head(*h)))
        .any(|l| crosses(l, qp.label_box))
    {
        hits |= 1 << 1;
    }
    if qp
        .lines
        .iter()
        .copied()
        .chain(qp.arrows.iter().flat_map(|h| super::leaders::head(*h)))
        .any(|l| crosses(l, p.label_box))
    {
        hits |= 1 << 2;
    }
    if q.kind == "callout"
        && qp.lines.iter().any(|l| {
            p.lines
                .iter()
                .chain(&p.arrows)
                .any(|t| super::leaders::lines_meet(*l, *t))
        })
    {
        hits |= 1 << 3;
    }
    if q.kind == "callout" && p.arrows.len() == 2 && p.lines.len() >= 3 {
        let extensions = [(0, p.lines[0]), (0, p.lines[1])];
        let (leader, text) =
            super::leaders::extension_contacts(&extensions, &qp.lines, qp.label_box);
        if leader > 0 {
            hits |= 1 << 5;
        }
        if text > 0 {
            hits |= 1 << 6;
        }
        if qp
            .arrows
            .iter()
            .flat_map(|a| super::leaders::head(*a))
            .any(|s| {
                p.lines[..2].iter().any(|[a, b]| {
                    super::leaders::touches_box(
                        s,
                        [
                            a[0].min(b[0]) - 0.2,
                            a[1].min(b[1]) - 0.2,
                            a[0].max(b[0]) + 0.2,
                            a[1].max(b[1]) + 0.2,
                        ],
                    )
                })
            })
        {
            hits |= 1 << 7;
        }
    }
    hits
}

// A label slide changes only its box and the dimension-line run-on. Measure
// all witness/head/owner contacts once for this lane; retain the complete mask.
struct LabelContacts<'a> {
    peer: &'a PlannedDimension,
    heads: Vec<[[f64; 2]; 2]>,
    constant: u8,
}
impl<'a> LabelContacts<'a> {
    fn new(lane: &Placed, peer: &'a PlannedDimension) -> Self {
        let Some(q) = &peer.placed else {
            return Self {
                peer,
                heads: Vec::new(),
                constant: 0,
            };
        };
        let mut constant = contacts(lane, peer) & ((1 << 4) | (1 << 5) | (1 << 6) | (1 << 7));
        if lane
            .lines
            .iter()
            .enumerate()
            .filter(|(n, _)| *n != 2)
            .any(|(_, s)| crosses(*s, q.label_box))
            || lane
                .arrows
                .iter()
                .flat_map(|a| super::leaders::head(*a))
                .any(|s| crosses(s, q.label_box))
        {
            constant |= 1 << 1;
        }
        if peer.kind == "callout"
            && q.lines.iter().any(|s| {
                lane.lines
                    .iter()
                    .enumerate()
                    .filter(|(n, _)| *n != 2)
                    .map(|(_, s)| s)
                    .chain(&lane.arrows)
                    .any(|t| super::leaders::lines_meet(*s, *t))
            })
        {
            constant |= 1 << 3;
        }
        Self {
            peer,
            constant,
            heads: q
                .arrows
                .iter()
                .flat_map(|a| super::leaders::head(*a))
                .collect(),
        }
    }
    fn contacts(&self, c: &Placed) -> u8 {
        let Some(q) = &self.peer.placed else { return 0 };
        let mut hits = self.constant;
        if boxes_overlap(c.label_box, q.label_box) {
            hits |= 1;
        }
        if hits & (1 << 1) == 0 && crosses(c.lines[2], q.label_box) {
            hits |= 1 << 1;
        }
        if q.lines
            .iter()
            .chain(&self.heads)
            .any(|s| crosses(*s, c.label_box))
        {
            hits |= 1 << 2;
        }
        if hits & (1 << 3) == 0
            && self.peer.kind == "callout"
            && q.lines
                .iter()
                .any(|s| super::leaders::lines_meet(*s, c.lines[2]))
        {
            hits |= 1 << 3;
        }
        hits
    }
}

/// Clearance against the original ink is invariant while testing label slides.
/// Keep the exact old-contact masks, then test only each candidate's new ink.
struct LabelClearance<'a> {
    peer: &'a Placed,
    padded: [f64; 4],
    overlap: bool,
    near: bool,
    peer_clear: Vec<[[f64; 2]; 2]>,
    moving_clear: Vec<usize>,
    moving_crossed: Vec<usize>,
    peer_crossed: Vec<[[f64; 2]; 2]>,
}

fn clearance_box(b: [f64; 4]) -> [f64; 4] {
    [b[0] - CLEAR, b[1] - CLEAR, b[2] + CLEAR, b[3] + CLEAR]
}

impl<'a> LabelClearance<'a> {
    fn new(original: &Placed, peer: &'a Placed) -> Self {
        let padded = clearance_box(peer.label_box);
        let was = clearance_box(original.label_box);
        Self {
            peer,
            padded,
            overlap: boxes_overlap(original.label_box, peer.label_box),
            near: boxes_overlap(original.label_box, padded),
            peer_clear: peer
                .lines
                .iter()
                .chain(&peer.arrows)
                .copied()
                .filter(|s| !crosses(*s, was))
                .collect(),
            moving_clear: original
                .lines
                .iter()
                .chain(&original.arrows)
                .enumerate()
                .filter_map(|(i, s)| (!crosses(*s, padded)).then_some(i))
                .collect(),
            moving_crossed: original
                .lines
                .iter()
                .enumerate()
                .filter_map(|(i, s)| crosses(*s, peer.label_box).then_some(i))
                .collect(),
            peer_crossed: peer
                .lines
                .iter()
                .copied()
                .filter(|s| crosses(*s, original.label_box))
                .collect(),
        }
    }

    fn clears(&self, candidate: &Placed) -> bool {
        if boxes_overlap(candidate.label_box, self.padded)
            && (!self.near
                || (self.overlap && !boxes_overlap(candidate.label_box, self.peer.label_box)))
        {
            return false;
        }
        let near = clearance_box(candidate.label_box);
        if self.peer_clear.iter().any(|s| crosses(*s, near)) {
            return false;
        }
        let stroke = |i: usize| {
            if i < candidate.lines.len() {
                candidate.lines.get(i)
            } else {
                candidate.arrows.get(i - candidate.lines.len())
            }
        };
        if self
            .moving_clear
            .iter()
            .filter_map(|&i| stroke(i))
            .any(|s| crosses(*s, self.padded))
        {
            return false;
        }
        self.moving_crossed
            .iter()
            .filter_map(|&i| candidate.lines.get(i))
            .all(|s| crosses(*s, self.peer.label_box) || !crosses(*s, self.padded))
            && self
                .peer_crossed
                .iter()
                .all(|s| crosses(*s, candidate.label_box) || !crosses(*s, near))
    }
}

/// A removed contact must leave readable paper, including when another stroke of the
/// same item remains close. Test strokes separately so existing witness proximity is kept.
#[cfg(test)]
fn clears_with_margin(p: &Placed, c: &Placed, q: &PlannedDimension) -> bool {
    let Some(qp) = &q.placed else { return true };
    let pad = |b: [f64; 4]| [b[0] - CLEAR, b[1] - CLEAR, b[2] + CLEAR, b[3] + CLEAR];
    if boxes_overlap(p.label_box, qp.label_box)
        && !boxes_overlap(c.label_box, qp.label_box)
        && boxes_overlap(c.label_box, pad(qp.label_box))
    {
        return false;
    }
    // Paper that was clear before the move stays clear: a stroke of another item that was
    // not already within CLEAR of the label may not be approached closer than CLEAR now.
    let (near, was) = (pad(c.label_box), pad(p.label_box));
    if boxes_overlap(c.label_box, pad(qp.label_box))
        && !boxes_overlap(p.label_box, pad(qp.label_box))
    {
        return false;
    }
    if qp
        .lines
        .iter()
        .chain(&qp.arrows)
        .any(|l| crosses(*l, near) && !crosses(*l, was))
    {
        return false;
    }
    if c.lines
        .iter()
        .chain(&c.arrows)
        .zip(p.lines.iter().chain(&p.arrows))
        .any(|(after, before)| {
            crosses(*after, pad(qp.label_box)) && !crosses(*before, pad(qp.label_box))
        })
    {
        return false;
    }
    p.lines.iter().zip(&c.lines).all(|(before, after)| {
        !crosses(*before, qp.label_box)
            || crosses(*after, qp.label_box)
            || !crosses(*after, pad(qp.label_box))
    }) && qp.lines.iter().all(|line| {
        !crosses(*line, p.label_box)
            || crosses(*line, c.label_box)
            || !crosses(*line, pad(c.label_box))
    })
}

fn outside(p: &Placed, area: [f64; 4]) -> bool {
    ink_points(p).any(|q| {
        q[0] < area[0] - 1e-6
            || q[1] < area[1] - 1e-6
            || q[0] > area[2] + 1e-6
            || q[1] > area[3] + 1e-6
    })
}

fn label_at(p: &Placed, at: f64) -> Placed {
    let k = axis(p);
    let mut c = p.clone();
    let d = at - p.label_box[k];
    c.label_at[k] += d;
    c.label_box[k] += d;
    c.label_box[k + 2] += d;
    let (lo, hi) = tips(p);
    // Reset the old run-on: the dimension line ends beside its own text.
    let outside = p
        .arrows
        .iter()
        .any(|[_, tail]| tail[k] < lo || tail[k] > hi);
    let reach = if outside { ARROW + 0.5 } else { 0.0 };
    c.lines[2][0][k] = lo - reach;
    c.lines[2][1][k] = hi + reach;
    if at < lo {
        c.lines[2][0][k] = c.lines[2][0][k].min(at + c.label_box[k + 2] - c.label_box[k] + 0.5);
    }
    if at > hi {
        c.lines[2][1][k] = c.lines[2][1][k].max(at - 0.5);
    }
    c
}

/// Keep the measured tips and put arrowheads outside when grown nominal text cannot
/// clear a crossing witness between inward heads. The longer line carries those heads.
pub(super) fn outward_heads(p: &Placed) -> Placed {
    let mut c = p.clone();
    let k = axis(p);
    let (lo, hi) = tips(p);
    for [tip, tail] in &mut c.arrows {
        if (tip[k] == lo && tail[k] > lo) || (tip[k] == hi && tail[k] < hi) {
            *tail = std::array::from_fn(|j| 2. * tip[j] - tail[j]);
        }
    }
    c.lines[2][0][k] = lo - ARROW - 0.5;
    c.lines[2][1][k] = hi + ARROW + 0.5;
    c
}

/// One extra paper clearance from its own dimension line, without changing the span.
fn lift_label(p: &Placed) -> Placed {
    let k = 1 - axis(p);
    let delta = (p.label_at[k] - p.arrows[0][0][k]).signum() * CLEAR;
    let mut c = p.clone();
    c.label_at[k] += delta;
    c.label_box[k] += delta;
    c.label_box[k + 2] += delta;
    c
}

/// Outside arrows can carry short text centred over their measured span.
/// Lift it half a millimetre to leave paper above the outward arrowheads.
pub(super) fn centered_short(p: &Placed) -> Option<Placed> {
    if p.arrows.len() != 2 || p.lines.len() < 3 {
        return None;
    }
    let k = axis(p);
    let (lo, hi) = tips(p);
    if !p
        .arrows
        .iter()
        .any(|[_, tail]| tail[k] < lo || tail[k] > hi)
    {
        return None;
    }
    let width = p.label_box[k + 2] - p.label_box[k];
    if width > hi - lo + 1e-6 {
        return None;
    }
    let mut c = label_at(p, 0.5 * (lo + hi - width));
    let normal = 1 - k;
    let lift = if k == 0 { 0.5 } else { -0.5 };
    c.label_at[normal] += lift;
    c.label_box[normal] += lift;
    c.label_box[normal + 2] += lift;
    Some(c)
}

fn alternatives(p: &Placed, drawable: [f64; 4], others: &[&PlannedDimension]) -> Vec<Placed> {
    alternatives_placed(
        p,
        drawable,
        &others
            .iter()
            .filter_map(|d| d.placed.as_ref())
            .collect::<Vec<_>>(),
    )
    .collect()
}

pub(super) fn alternatives_placed<'a>(
    p: &'a Placed,
    drawable: [f64; 4],
    others: &[&Placed],
) -> impl Iterator<Item = Placed> + use<'a> {
    let k = axis(p);
    let (lo, hi) = tips(p);
    let width = p.label_box[k + 2] - p.label_box[k];
    let outside_arrows = p
        .arrows
        .iter()
        .any(|[_, tail]| tail[k] < lo || tail[k] > hi);
    let clear = if outside_arrows { 0.5 } else { ARROW + 0.5 };
    let low = (lo + clear).max(drawable[k]);
    let high = (hi - clear - width).min(drawable[k + 2] - width);
    let (mut positions, mut tail) = (Vec::new(), Vec::new());
    if low <= high {
        let centre = (0.5 * (lo + hi - width)).clamp(low, high);
        positions.extend([centre, low, high]);
        positions.push(p.label_box[k].clamp(low, high));
        positions.retain(|at| *at >= low && *at <= high);
        // Peers' edges, kept only within the span (the order sorted below is total).
        let within = |at: &f64| *at >= low && *at <= high;
        for q in others {
            positions.extend(
                [q.label_box[k] - CLEAR - width, q.label_box[k + 2] + CLEAR]
                    .into_iter()
                    .filter(within),
            );
            for [a, b] in &q.lines {
                positions.extend(
                    [a[k].min(b[k]) - CLEAR - width, a[k].max(b[k]) + CLEAR]
                        .into_iter()
                        .filter(within),
                );
            }
        }
        if outside_arrows {
            // Text inside the span comes first; failing that, just past an arrow tail.
            tail.extend([hi + ARROW + 1.0, lo - ARROW - 1.0 - width]);
        }
    } else {
        let gap = if outside_arrows { ARROW + 1.0 } else { 1.0 };
        positions.extend([hi + gap, lo - gap - width]);
    }
    positions.sort_by(|a, b| {
        (a - p.label_box[k])
            .abs()
            .total_cmp(&(b - p.label_box[k]).abs())
            .then(a.total_cmp(b))
    });
    positions.dedup_by(|a, b| (*a - *b).abs() < 1e-6);
    let overhang = if outside_arrows {
        vec![lo - CLEAR, hi + CLEAR - width]
    } else {
        Vec::new()
    };
    positions
        .into_iter()
        .chain(overhang)
        .chain(tail)
        .filter(move |at| *at >= drawable[k] && at + width <= drawable[k + 2])
        .map(move |at| label_at(p, at))
        .filter(|c| c.lines.iter().take(2).all(|s| !crosses(*s, c.label_box)))
}

fn promote(p: &Placed, amount: f64) -> Placed {
    let k = 1 - axis(p);
    let line = p.arrows[0][0][k];
    let out = (line - p.lines[0][0][k]).signum();
    shift_lane(p, out * amount)
}

/// Move the dimension line and text while retaining its measured witness origins.
pub(crate) fn in_lane(p: &Placed, line: f64) -> Placed {
    let k = 1 - axis(p);
    shift_lane(p, line - p.arrows[0][0][k])
}

fn shift_lane(p: &Placed, d: f64) -> Placed {
    let k = 1 - axis(p);
    let mut c = p.clone();
    for l in c.lines.iter_mut().take(2) {
        l[1][k] += d;
    }
    for l in c.lines.iter_mut().skip(2).chain(&mut c.arrows) {
        for q in l {
            q[k] += d;
        }
    }
    c.label_at[k] += d;
    c.label_box[k] += d;
    c.label_box[k + 2] += d;
    c
}

pub(crate) fn settle_inventory(
    analysis: &Analysis,
    spec: &Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    inventory: &mut [PlannedDimension],
    fixed: &[PlannedDimension],
) {
    // Only resolved ink, ownership/chain semantics and pinning affect this
    // pass. Presentation metadata remains on the caller's fresh inventory.
    let geometry = |items: &[PlannedDimension]| {
        items
            .iter()
            .map(|d| {
                (
                    d.id.clone(),
                    d.kind.clone(),
                    d.view.clone(),
                    d.region,
                    d.measurements.clone(),
                    d.placed.clone(),
                )
            })
            .collect::<Vec<_>>()
    };
    let pins: Vec<_> = spec
        .dimensions
        .iter()
        .filter_map(|(id, d)| d.place.as_ref().is_some_and(|p| p.pinned).then_some(id))
        .collect();
    let page = Page::new(analysis, sheet, views);
    let key = serde_json::to_vec(&(pins, page, geometry(inventory), geometry(fixed)))
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        .expect("label settlement inputs serialize");
    let next: Vec<_> = crate::planning::memo(analysis, "settled-label-inventory", key, || {
        let mut next = inventory.to_vec();
        settle_inventory_uncached(analysis, spec, sheet, views, &mut next, fixed);
        next.into_iter().map(|d| d.placed).collect::<Vec<_>>()
    });
    for (d, placed) in inventory.iter_mut().zip(next) {
        d.placed = placed;
    }
}

fn settle_inventory_uncached(
    analysis: &Analysis,
    spec: &Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    inventory: &mut [PlannedDimension],
    fixed: &[PlannedDimension],
) {
    let page = Page::new(analysis, sheet, views);
    let bounded_notches = !crate::notches::requirements(analysis).is_empty();
    // One traversal: the next lane, or one rung further if a peer occupies that lane.
    for i in 0..inventory.len() {
        let d = &inventory[i];
        if d.kind != "linear"
            || d.region == Some(super::CandidateRegion::Interior)
            || spec
                .dimensions
                .get(&d.id)
                .and_then(|e| e.place.as_ref())
                .is_some_and(|p| p.pinned)
        {
            continue;
        }
        let Some(p) = &d.placed else { continue };
        // These alternatives slide one page coordinate. An oblique span must retain
        // its perpendicular witnesses and a straight dimension line.
        if p.arrows.len() != 2
            || p.lines.len() < 3
            || !(p.rotation == 0.0 || p.rotation.abs() == 90.0)
        {
            continue;
        }
        let view = d.view.strip_prefix("v:").unwrap_or(&d.view);
        let area = page.area(view).unwrap_or(page.drawable());
        let others: Vec<_> = inventory
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .map(|(_, q)| q)
            .chain(fixed)
            .filter(|q| q.placed.is_some())
            .collect();
        // Promotion uses room already occupied by this view's dimensions or reserved by
        // its bands; it never grows a dense tower past that footprint.
        let region = inventory
            .iter()
            .filter(|q| q.view == d.view)
            .filter_map(|q| q.placed.as_ref())
            .flat_map(ink_points)
            .fold(area, |b, q| {
                [
                    b[0].min(q[0]),
                    b[1].min(q[1]),
                    b[2].max(q[0]),
                    b[3].max(q[1]),
                ]
            });
        let peer_bounds: Vec<_> = others
            .iter()
            // Invariant: The peer collection was filtered to dimensions with placements.
            .map(|q| ink_bounds(q.placed.as_ref().expect("placed peer")))
            .collect();
        let original_bounds = contact_bounds(p);
        let old: Vec<_> = others
            .iter()
            .zip(&peer_bounds)
            .map(|(q, b)| {
                if boxes_overlap(original_bounds, *b) {
                    contacts(p, q)
                } else {
                    0
                }
            })
            .collect();
        let count: usize = old.iter().map(|hits| hits.count_ones() as usize).sum();
        let span_bad = |c: &Placed| {
            off_span(
                c,
                others
                    .iter()
                    .filter(|q| q.kind == "linear" && q.view == d.view)
                    .filter_map(|q| q.placed.as_ref()),
            )
        };
        let original_span_bad = span_bad(p);
        let normal = 1 - axis(p);
        let centre = views
            .iter()
            .find(|v| v.id == d.view)
            .map_or(0., |v| v.at[normal]);
        let extent_inward = |c: &Placed| {
            super::is_envelope_extent(&d.id)
                && others
                    .iter()
                    .filter(|q| {
                        q.kind == "linear" && q.view == d.view && !super::is_envelope_extent(&q.id)
                    })
                    .filter_map(|q| q.placed.as_ref())
                    .any(|q| {
                        q.arrows.len() == 2
                            && q.rotation == c.rotation
                            && (q.arrows[0][0][normal] - centre) * (c.arrows[0][0][normal] - centre)
                                > 0.
                            && (c.arrows[0][0][normal] - centre).abs()
                                <= (q.arrows[0][0][normal] - centre).abs() + 1e-6
                    })
        };
        let restore_extent = extent_inward(p);
        if count == 0 && !original_span_bad && !outside(p, area) && !restore_extent {
            continue;
        }
        // Whether the original ink already detached a peer's text is constant
        // throughout every slide and lane trial for this one dimension.
        let protected_spans: Vec<_> = others
            .iter()
            .filter(|q| q.kind == "linear" && q.view == d.view)
            .filter_map(|q| q.placed.as_ref())
            .filter(|q| !off_span(q, std::iter::once(p)))
            .collect();
        let clearance: Vec<_> = others.iter().map(|_| std::cell::OnceCell::new()).collect();
        let rank = |d: &PlannedDimension| {
            let role = d.id.rsplit_once('/').map_or("", |(_, role)| role);
            if super::is_envelope_extent(&d.id) {
                2
            } else if role.starts_with("location")
                || role.starts_with("step_height")
                || role.starts_with("step_position")
            {
                1
            } else {
                0
            }
        };
        // Ranks are fixed for this dimension and its peers: work them out once.
        let own_rank = rank(d);
        let ranks: Vec<_> = others.iter().map(|q| rank(q)).collect();
        let allowed = |c: &Placed, prepared: &[LabelContacts]| {
            // A longer label may need another lane, but must not jump past an overall
            // extent or reverse datum-location order. Same-rank sizes may still skip
            // an occupied rung, as before.
            // Sliding text on its existing line cannot change lane order.
            // An inherited imperfect ladder must not block that local repair.
            let ordered = (crate::nominal::geometry_eligible(analysis)
                && !crate::nominal::comparison()
                && (c.arrows[0][0][normal] - p.arrows[0][0][normal]).abs() < 1e-6)
                || others
                    .iter()
                    .zip(&ranks)
                    .filter(|(q, _)| q.kind == "linear" && q.view == d.view)
                    .filter_map(|(q, rank)| Some((*q, *rank, q.placed.as_ref()?)))
                    .filter(|(_, _, q)| {
                        q.arrows.len() == 2
                            && q.rotation == p.rotation
                            && (q.arrows[0][0][normal] - centre) * (p.arrows[0][0][normal] - centre)
                                > 0.
                    })
                    .all(|(q, rank, placed)| {
                        let order = own_rank.cmp(&rank).then_with(|| {
                            if own_rank == 1 {
                                d.value.total_cmp(&q.value)
                            } else {
                                std::cmp::Ordering::Equal
                            }
                        });
                        let next = (c.arrows[0][0][normal] - centre).abs();
                        let peer = (placed.arrows[0][0][normal] - centre).abs();
                        match order {
                            std::cmp::Ordering::Less => next < peer - 1e-6,
                            std::cmp::Ordering::Greater => next > peer + 1e-6,
                            std::cmp::Ordering::Equal => true,
                        }
                    });
            if !ordered
                || !page.fits(c, view)
                || (!restore_extent && outside(c, region))
                || (!restore_extent && outside(c, area) && !outside(p, area))
            {
                return false;
            }
            let bounds = contact_bounds(c);
            others
                .iter()
                .zip(&old)
                .zip(&peer_bounds)
                .zip(prepared)
                .zip(&clearance)
                .all(|((((q, before), peer), cached), margin)| {
                    if !boxes_overlap(bounds, *peer) {
                        return true;
                    }
                    let contact = cached.contacts(c);
                    contact & (1 << 4) == 0
                        && contact & !before == 0
                        && margin
                            .get_or_init(|| {
                                // Invariant: The peer collection was filtered to dimensions with placements.
                                LabelClearance::new(p, q.placed.as_ref().expect("placed peer"))
                            })
                            .clears(c)
                })
                && protected_spans
                    .iter()
                    .all(|q| !off_span(q, std::iter::once(c)))
        };
        let better = |c: &Placed, prepared: &[LabelContacts]| {
            let bounds = contact_bounds(c);
            let hits: usize = others
                .iter()
                .zip(&peer_bounds)
                .zip(prepared)
                .filter(|((_, b), _)| boxes_overlap(bounds, **b))
                .map(|((_, _), cached)| cached.contacts(c).count_ones() as usize)
                .sum();
            // Notch lanes may retain an existing detached station. Score its real
            // span tier so an unchanged candidate cannot stop a later repair.
            // Other drawings still require !span_bad(c) below.
            (
                hits,
                extent_inward(c),
                bounded_notches && span_bad(c),
                outside(c, area),
            ) < (count, restore_extent, original_span_bad, outside(p, area))
        };
        // A single occupied rung may be skipped; fixed-ink obstructions never justify
        // a farther search. Two paper lanes is the absolute movement bound.
        let next = promote(p, lane_pitch());
        let next_occupied = others.iter().any(|q| shared_span_contact(&next, q));
        let chained = others.iter().any(|q| {
            q.view == d.view
                && super::placement::slot_chain_claims(&d.measurements, &q.measurements)
                && q.placed
                    .as_ref()
                    .is_some_and(|q| super::placement::forms_chain(p, q))
        });
        let found = (0..=if chained {
            0
        } else if next_occupied || restore_extent {
            2
        } else {
            1
        })
            // Restoring an overall may spend two outward lanes beyond the old
            // footprint. Page fit, every contact and peer clearance still gate it.
            .take_while(|n| {
                restore_extent || !outside(&promote(p, *n as f64 * lane_pitch()), region)
            })
            .find_map(|n| {
                let lane = promote(p, n as f64 * lane_pitch());
                let flexible = outside(p, area)
                    || others
                        .iter()
                        .any(|q| q.kind == "linear" && contacts(p, q) != 0);
                let outward = flexible.then(|| outward_heads(&lane));
                std::iter::once(lane).chain(outward).find_map(|lane| {
                    let prepared: Vec<_> = others
                        .iter()
                        .map(|q| LabelContacts::new(&lane, q))
                        .collect();
                    let alternatives = alternatives(&lane, page.drawable(), &others);
                    alternatives
                        .iter()
                        .filter(|c| bounded_notches && span_bad(p) && !span_bad(c))
                        .flat_map(|c| [c.clone(), lift_label(c)])
                        .chain(std::iter::once(lane.clone()).filter(|_| bounded_notches))
                        .chain(centered_short(&lane))
                        .chain(alternatives.iter().flat_map(|c| {
                            std::iter::once(c.clone()).chain(flexible.then(|| lift_label(c)))
                        }))
                        .find(|c| {
                            allowed(c, &prepared)
                                && better(c, &prepared)
                                && (!span_bad(c) || (bounded_notches && span_bad(p)))
                        })
                })
            });
        if let Some(c) = found {
            inventory[i].placed = Some(c);
        }
    }
}

/// A strip's dimension line: (view, normal axis, side), its distance from the view's centre,
/// and the inventory indices drawn on it.
type Line = ((String, usize, bool), f64, Vec<usize>);

/// Close a strip's lanes after layout. Dimensions on one dimension line move together. Taking
/// the strip's lines inner first, each is tried in the lanes inside its own, from the strip's
/// innermost lane outward, and moves to the first one where no dimension already there comes
/// within a clearance of it (so ends never meet as a false chain) and every member's
/// collisions are no worse, code by code: a move can never add a finding. A line stays
/// outside every dimension it overlaps that already lies inside it (sizes stay nearest, a
/// baseline keeps its order). Dimensions the author placed or pinned, and interior ones, do
/// not move. It runs on the complete inventory, so omitting an item moves no other; each move
/// is returned as (view, normal axis, old line, new line), so an item drawn outside the
/// inventory on a moved line (one promoted into an omitted item's lane) can follow it
/// ([`follow_lanes`]).
pub(crate) fn compact_inventory(
    analysis: &Analysis,
    spec: &Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    inventory: &mut [PlannedDimension],
    fixed: &[PlannedDimension],
    cache: &super::footprints::Cache,
) -> Vec<(String, usize, f64, f64)> {
    let mut moves = Vec::new();
    let pitch = lane_pitch();
    let straight = |d: &PlannedDimension| {
        d.kind == "linear"
            && d.placed.as_ref().is_some_and(|p| {
                p.arrows.len() == 2
                    && p.lines.len() >= 3
                    && (p.rotation == 0.0 || p.rotation.abs() == 90.0)
            })
    };
    let movable = |d: &PlannedDimension| {
        straight(d)
            && d.region != Some(super::CandidateRegion::Interior)
            && spec
                .dimensions
                .get(&d.id)
                .is_none_or(|e| e.place.is_none() && e.view.is_none())
            && views
                .iter()
                .any(|v| v.id == d.view && v.sheet == sheet.number)
    };
    // (view, normal axis, side) and the dimension line's distance from the view's centre.
    let strip = |d: &PlannedDimension| {
        let p = d.placed.as_ref()?;
        let k = 1 - axis(p);
        let centre = views.iter().find(|v| v.id == d.view)?.at[k];
        let at = p.arrows[0][0][k];
        Some((
            (d.view.clone(), k, at > centre),
            (at - centre).abs(),
            centre,
        ))
    };
    let footprint = |p: &Placed| {
        let k = axis(p);
        let (lo, hi) = tips(p);
        (
            lo.min(p.label_box[k]) - CLEAR,
            hi.max(p.label_box[k + 2]) + CLEAR,
        )
    };
    let meets = |a: (f64, f64), b: (f64, f64)| a.1 >= b.0 && b.1 >= a.0;
    let n = inventory.len();
    // Lines: straight dimensions of one strip on one dimension line.
    let mut lines: Vec<Line> = Vec::new();
    for i in (0..n).filter(|&i| straight(&inventory[i])) {
        let Some((key, reach, _)) = strip(&inventory[i]) else {
            continue;
        };
        match lines
            .iter_mut()
            .find(|(k, r, _)| *k == key && (r - reach).abs() < 1e-6)
        {
            Some(line) => line.2.push(i),
            None => lines.push((key, reach, vec![i])),
        }
    }
    lines.sort_by(|a, b| a.1.total_cmp(&b.1));
    let mut all: Vec<_> = inventory.iter().chain(fixed).cloned().collect();
    let counts = |problems: Vec<crate::model::Problem>| {
        let mut c = std::collections::BTreeMap::<String, usize>::new();
        for q in problems {
            *c.entry(q.code).or_default() += 1;
        }
        c
    };
    // Only placements change below; every trial uses the same projection proofs.
    let mut geometry = None;
    for l in 0..lines.len() {
        let (key, reach, members) = lines[l].clone();
        if !members.iter().all(|&i| movable(&inventory[i])) {
            continue;
        }
        let spans: Vec<_> = members
            .iter()
            .filter_map(|&i| all[i].placed.as_ref().map(footprint))
            .collect();
        let others: Vec<_> = lines
            .iter()
            .enumerate()
            .filter(|(m, (k, _, _))| *m != l && *k == key)
            .map(|(_, (_, r, ms))| {
                let f: Vec<_> = ms
                    .iter()
                    .filter_map(|&j| all[j].placed.as_ref().map(footprint))
                    .collect();
                (*r, f)
            })
            .collect();
        let touches = |f: &[(f64, f64)]| spans.iter().any(|a| f.iter().any(|b| meets(*a, *b)));
        let inner = others.iter().map(|(r, _)| *r).fold(reach, f64::min);
        let floor = others
            .iter()
            .filter(|(r, f)| *r < reach - 1e-6 && touches(f))
            .map(|(r, _)| r + pitch)
            .fold(inner, f64::max);
        let mut before = None;
        let sign = if key.2 { 1.0 } else { -1.0 };
        let originals: Vec<_> = members.iter().map(|&i| all[i].placed.clone()).collect();
        let mut lane = floor;
        while lane < reach - 0.5 * pitch {
            let occupied = others
                .iter()
                .any(|(r, f)| (r - lane).abs() < 0.5 * pitch && touches(f));
            if !occupied {
                let geometry = geometry.get_or_insert_with(|| {
                    super::placement::CollisionGeometry::new(analysis, &all, sheet, views, cache)
                });
                // A packed strip has no move to adjudicate. Measure its
                // baseline only when an unoccupied inner lane can be tried.
                let before = before.get_or_insert_with(|| {
                    members
                        .iter()
                        .map(|&i| {
                            counts(geometry.for_item(analysis, &all, sheet, views, &all[i].id))
                        })
                        .collect::<Vec<_>>()
                });
                for &i in &members {
                    // Invariant: lines hold placed dimensions only.
                    let p = all[i].placed.clone().expect("placed dimension");
                    let (_, _, centre) = strip(&all[i]).expect("strip of a placed dimension");
                    all[i].placed = Some(in_lane(&p, centre + sign * lane));
                }
                let free = members.iter().zip(before.iter()).all(|(&i, was)| {
                    counts(geometry.for_item(analysis, &all, sheet, views, &all[i].id))
                        .iter()
                        .all(|(code, n)| was.get(code).is_some_and(|m| n <= m))
                });
                if free {
                    for &i in &members {
                        inventory[i].placed = all[i].placed.clone();
                    }
                    // Invariant: lines hold placed dimensions only.
                    let centre = strip(&all[members[0]])
                        .expect("strip of a placed dimension")
                        .2;
                    moves.push((
                        key.0.clone(),
                        key.1,
                        centre + sign * reach,
                        centre + sign * lane,
                    ));
                    lines[l].1 = lane;
                    break;
                }
                for (&i, p) in members.iter().zip(&originals) {
                    all[i].placed = p.clone();
                }
            }
            lane += pitch;
        }
    }
    moves
}

/// Moves the dimensions drawn outside the inventory that lie on a line [`compact_inventory`]
/// moved, by the same amount.
pub(crate) fn follow_lanes(
    dimensions: &mut [PlannedDimension],
    moves: &[(String, usize, f64, f64)],
) {
    for d in dimensions {
        let Some(p) = &d.placed else { continue };
        if d.kind != "linear" || p.arrows.len() != 2 || p.lines.len() < 3 {
            continue;
        }
        let k = 1 - axis(p);
        let at = p.arrows[0][0][k];
        if let Some((_, _, _, to)) = moves
            .iter()
            .find(|(view, axis, from, _)| *view == d.view && *axis == k && (from - at).abs() < 1e-6)
        {
            d.placed = Some(in_lane(p, *to));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model;
    use serde_json::json;

    fn dimension(id: &str, kind: &str, value: f64, p: Placed) -> PlannedDimension {
        serde_json::from_value(json!({
            "id": id, "kind": kind, "view": "v:front", "value": value,
            "label": value.to_string(), "terms": [value.to_string()],
            "measurements": [{"feature": id, "parameter": "length", "value": value}],
            "default": true, "from_spec": [], "basis": "synthetic ink probe",
            "state": {"kind": "shown"}, "placed": p
        }))
        .unwrap()
    }

    #[test]
    fn separated_ink_bounds_exclude_every_contact_and_clearance_constraint() {
        let original = Placed {
            lines: vec![
                [[0., 0.], [0., 20.]],
                [[12., 0.], [12., 20.]],
                [[0., 18.], [12., 18.]],
            ],
            arrows: vec![[[0., 18.], [2.7, 18.]], [[12., 18.], [9.3, 18.]]],
            label_at: [2., 19.],
            label_box: [2., 19., 10., 22.],
            rotation: 0.,
        };
        for vertical in [false, true] {
            for arrows in 0..=2 {
                for n in -15..=15 {
                    let mut peer = original.clone();
                    for p in peer.lines.iter_mut().chain(&mut peer.arrows).flatten() {
                        p[0] += f64::from(n) * 3.;
                        p[1] += f64::from(n) * 0.7;
                        if vertical {
                            p.swap(0, 1);
                        }
                    }
                    let at = [f64::from(n) * 3., f64::from(n) * 0.7];
                    peer.label_box = [at[0] + 2., at[1] + 19., at[0] + 10., at[1] + 22.];
                    peer.arrows.truncate(arrows);
                    peer.rotation = if vertical { 90. } else { 0. };
                    let kind = if arrows == 2 { "linear" } else { "callout" };
                    let q = dimension("peer", kind, 12., peer);
                    let bounds = ink_bounds(q.placed.as_ref().unwrap());
                    for n in 0..=2 {
                        let lane = promote(&original, f64::from(n) * lane_pitch());
                        let prepared = LabelContacts::new(&lane, &q);
                        let clearance = LabelClearance::new(&original, q.placed.as_ref().unwrap());
                        for at in -20..=20 {
                            let c = label_at(&lane, f64::from(at));
                            assert_eq!(prepared.contacts(&c), contacts(&c, &q));
                            assert_eq!(clearance.clears(&c), clears_with_margin(&original, &c, &q));
                        }
                    }
                    for m in -20..=20 {
                        let mut moved = original.clone();
                        let shift = f64::from(m) * 2.;
                        for p in moved.lines.iter_mut().chain(&mut moved.arrows).flatten() {
                            p[0] += shift;
                        }
                        moved.label_box[0] += shift;
                        moved.label_box[2] += shift;
                        if !boxes_overlap(contact_bounds(&moved), bounds) {
                            assert_eq!(contacts(&moved, &q), 0);
                            assert!(clears_with_margin(&original, &moved, &q));
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn a_linear_move_preserves_the_full_leader_extension_corridor() {
        let p = Placed {
            lines: vec![
                [[20.0, 10.0], [20.0, 32.0]],
                [[40.0, 10.0], [40.0, 32.0]],
                [[20.0, 30.0], [40.0, 30.0]],
            ],
            arrows: vec![[[20.0, 30.0], [22.7, 30.0]], [[40.0, 30.0], [37.3, 30.0]]],
            label_at: [25.0, 31.0],
            label_box: [25.0, 31.0, 35.0, 33.0],
            rotation: 0.0,
        };
        let q = dimension(
            "d:leader",
            "callout",
            1.0,
            Placed {
                lines: vec![
                    [[20.75, 11.0], [23.75, 25.0]],
                    [[23.75, 25.0], [26.75, 25.0]],
                ],
                arrows: vec![],
                label_at: [28.0, 24.0],
                label_box: [28.0, 24.0, 34.0, 27.0],
                rotation: 0.0,
            },
        );
        let near = contacts(&p, &q);
        assert_eq!(near & (1 << 3), 0, "the strokes themselves do not cross");
        assert_ne!(
            near & (1 << 5),
            0,
            "a 0.75 mm gap is inside the reserved corridor"
        );
        let mut clear = p.clone();
        clear.lines[0] = [[18.0, 10.0], [18.0, 32.0]];
        assert_eq!(contacts(&clear, &q) & (1 << 5), 0);
    }

    #[test]
    fn promotion_uses_only_next_lane_preserves_pins_and_is_idempotent() {
        let analysis = model::analyse(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/plate.step"),
        )
        .unwrap()
        .1;
        let sheet: PlannedSheet = serde_json::from_value(json!({
            "size": "A4", "page": [297, 210], "scale": "1:1", "scale_factor": 1,
            "arrangement": "columns", "title_block": [200, 10, 287, 40],
            "default": true, "basis": "", "state": {"kind": "shown"}, "title": {}
        }))
        .unwrap();
        let mut size = dimension(
            "d:size/length",
            "linear",
            20.0,
            Placed {
                lines: vec![
                    [[20.0, 10.0], [20.0, 32.0]],
                    [[40.0, 10.0], [40.0, 32.0]],
                    [[20.0, 30.0], [40.0, 30.0]],
                ],
                arrows: vec![[[20.0, 30.0], [22.7, 30.0]], [[40.0, 30.0], [37.3, 30.0]]],
                label_at: [25.0, 31.0],
                label_box: [25.0, 31.0, 35.0, 33.0],
                rotation: 0.0,
            },
        );
        size.label = "20 ±1".into();
        size.terms = vec!["20".into(), "±1".into()];
        let peer = dimension(
            "d:peer/length",
            "linear",
            40.0,
            Placed {
                lines: vec![
                    [[15.0, 10.0], [15.0, 46.0]],
                    [[55.0, 10.0], [55.0, 46.0]],
                    [[15.0, 44.0], [55.0, 44.0]],
                ],
                arrows: vec![[[15.0, 44.0], [17.7, 44.0]], [[55.0, 44.0], [52.3, 44.0]]],
                label_at: [43.0, 45.0],
                label_box: [43.0, 45.0, 49.0, 47.0],
                rotation: 0.0,
            },
        );
        let mut datum = dimension(
            "pmi:datum:test",
            "callout",
            0.0,
            Placed {
                lines: vec![[[30.0, 10.0], [30.0, 33.0]]],
                arrows: vec![],
                label_at: [50.0, 33.0],
                label_box: [50.0, 33.0, 56.0, 35.0],
                rotation: 0.0,
            },
        );
        datum.label = "D".into();
        datum.terms = vec!["D".into()];
        datum.measurements.clear();
        let original = vec![size, peer];
        let fixed = vec![datum];
        let spec: Spec = serde_json::from_value(json!({
            "dimensions": {"d:peer/length": {"place": {"pinned": true}}}
        }))
        .unwrap();
        let p = original[0].placed.as_ref().unwrap();
        assert!(crosses(
            fixed[0].placed.as_ref().unwrap().lines[0],
            p.label_box
        ));
        assert!(
            alternatives(p, [10.0, 10.0, 287.0, 200.0], &[&fixed[0]])
                .iter()
                .all(|c| contacts(c, &fixed[0]) != 0),
            "no along-span alternative is clear"
        );
        let mut items = original.clone();
        // The synthetic ink starts on the frame line: hold this lane rule apart from the frame
        // gutter (2026-10-25), which would otherwise put it outside the drawable area.
        crate::layout::with_gutter(0.0, || {
            settle_inventory(&analysis, &spec, &sheet, &[], &mut items, &fixed)
        });
        let c = items[0].placed.as_ref().unwrap();
        // The next lane clears the short leader; the farther peer is untouched.
        assert_eq!(c.arrows[0][0][1], 37.0);
        assert_eq!(
            c.arrows.iter().map(|a| a[0][0]).collect::<Vec<_>>(),
            vec![20.0, 40.0]
        );
        assert_eq!(
            c.lines[..2].iter().map(|l| l[0]).collect::<Vec<_>>(),
            p.lines[..2].iter().map(|l| l[0]).collect::<Vec<_>>()
        );
        assert_eq!(items[0].measurements, original[0].measurements);
        assert_eq!(items[1], original[1]);
        assert!(contacts(c, &fixed[0]) == 0 && contacts(c, &items[1]) == 0);
        let settled = items.clone();
        crate::layout::with_gutter(0.0, || {
            settle_inventory(&analysis, &spec, &sheet, &[], &mut items, &fixed)
        });
        assert_eq!(items, settled);
        let mut occupied = original.clone();
        occupied[1].placed = Some(promote(occupied[1].placed.as_ref().unwrap(), -lane_pitch()));
        crate::layout::with_gutter(0.0, || {
            settle_inventory(&analysis, &spec, &sheet, &[], &mut occupied, &fixed)
        });
        assert_eq!(
            occupied[0].placed.as_ref().unwrap().arrows[0][0][1],
            44.0,
            "only one occupied rung may be skipped"
        );
        // A blocked next lane is not permission to skip the tower for a distant free lane.
        let mut blocked = fixed.clone();
        let obstacle = blocked[0].placed.as_mut().unwrap();
        obstacle.lines[0][1][1] = 35.0;
        obstacle.label_box = [27.0, 35.0, 33.0, 41.0];
        obstacle.label_at = [27.0, 35.0];
        let mut trapped = original.clone();
        crate::layout::with_gutter(0.0, || {
            settle_inventory(&analysis, &spec, &sheet, &[], &mut trapped, &blocked)
        });
        assert_eq!(
            trapped, original,
            "retain collision rather than skip occupied lanes"
        );

        let mut sliding = original.clone();
        let size = sliding[0].placed.as_mut().unwrap();
        size.lines[1][0][0] = 60.0;
        size.lines[1][1][0] = 60.0;
        size.lines[2][1][0] = 60.0;
        size.arrows[1] = [[60.0, 30.0], [57.3, 30.0]];
        size.label_at[0] = 35.0;
        size.label_box = [35.0, 31.0, 45.0, 33.0];
        let mut corridor = fixed.clone();
        for point in &mut corridor[0].placed.as_mut().unwrap().lines[0] {
            point[0] = 39.0;
        }
        crate::layout::with_gutter(0.0, || {
            settle_inventory(&analysis, &spec, &sheet, &[], &mut sliding, &corridor)
        });
        let b = sliding[0].placed.as_ref().unwrap().label_box;
        assert!(
            b[0] - 39.0 >= 1.0 - 1e-6 || 39.0 - b[2] >= 1.0 - 1e-6,
            "a cleared label must not butt against the leader"
        );

        let mut pinned_spec = spec;
        pinned_spec.dimensions.insert(
            "d:size/length".into(),
            serde_json::from_value(json!({"place": {"pinned": true}})).unwrap(),
        );
        let mut pinned = original.clone();
        settle_inventory(&analysis, &pinned_spec, &sheet, &[], &mut pinned, &fixed);
        assert_eq!(pinned, original, "an infeasible pin remains visible");
    }
}
