//! Callout leaders: draftwright's shared feature-leader placement (`annotations/leaders.py`, with
//! the candidate producers of `holes.py`, `hole_leader_candidates.py`, `_edge_callouts.py` and
//! `_diameters.py`) for the exterior region: a simplified re-implementation of its candidates and
//! objective, not a line-by-line port (its occupancy grid, probe budgets and strip solve are not
//! here).
//!
//! Every callout is a job with a bounded inventory of candidate leaders around its view. A
//! candidate is a tip on a site's rim (radial to the elbow, `holes._rim_tip`), an elbow outside
//! the view, a horizontal shoulder and the label beyond it, on the side the leader runs to
//! (draftwright's `text_side`). The inventory unites draftwright's producers:
//! - **columns** beside the view (its plan and side hole callouts, `_leader_anchors`; its
//!   column of z-turned diameters, `_diameter_column_left`): the elbow just past the view's left
//!   or right edge, or past the dimensions that strip holds, on a row within the view's height,
//!   the site's own row first;
//! - **rows** above and below the view (its front hole callouts, `FrontHoleLeaderCandidateAdapter`;
//!   its row of x-turned diameters, `_diameter_row_below`): the elbow just past the top or bottom
//!   edge, or past that strip's dimensions, at a column within the view's width, the site's own
//!   column first, the label right or left;
//! - **escapes** (its corner and radial candidates for fillets, chamfers and flats,
//!   `_corner_escape_candidates`, `_radial_candidates`): from each site out along sixteen
//!   directions, the elbow a little past where the ray leaves the view.
//!
//! Each retained candidate is measured analytically (`leader_callout_geometry`): its cost is its ink's
//! length (leader and shoulder, plus travel inside the solid bounds to prefer a near exit),
//! in micrometres so sub-micron noise
//! cannot reorder candidates (ISSUE-740-b). Its blockers are counted in two tiers:
//! - **hard** (`_hard_fixed_blockers`): ink off the drawing area; the label or a line in the
//!   title block; the label or shoulder over any view (the exterior region); the leader across
//!   another view, or back into the part's material after leaving it at its tip (each solid's
//!   box, measured by [`crate::material::reentry`], the predicate lint's
//!   `leader_crosses_silhouette` shares, ADR2-8; its depth is also added to the length); a leader
//!   longer than [`MAX_LEADER`];
//! - **crossings** (Policy B, ADR2-D6): one per other item met and kind of contact, as the
//!   plan's collision checks report one problem per pair and code: an item whose line the
//!   leader or shoulder crosses or runs along, and (again) one whose ink and the label meet (its
//!   line through the label, the label on its label, a leader line through its label);
//!   each other feature footprint crossed by the shaft and ink outside the reserved area also
//!   count in this tier. The original assignment is retained when the measured candidates and their pair
//!   contacts find more annotation defects in the new assignment;
//!   the dimensions are placed first and fixed (ARCH0014-3: principal dimensions claim their
//!   strips; the callouts yield);
//! - **text**: of the items met, those whose ink and the label meet, below the crossings:
//!   between as many crossings, a leader across a line is kept over text on a line (draftwright
//!   protects text both ways, `annotation_text_ink_clear`);
//!
//! The original assignment keeps its area tier below text; the footprint-aware assignment
//! counts it with crossings, because either contact is an annotation defect.
//!
//! The jobs are then assigned jointly (ISSUE-740-a, `_assign_by_view`): each takes one candidate,
//! minimising first the hard blockers, then the crossings (with the fixed ink and between the
//! chosen leaders, where one leader's ink on another's label counts two: [`conflict`]), then the
//! text touched, then the ink out of its area, then the total length.
//! The jobs split into independent groups that could conflict; each group is solved exactly by a
//! branch and bound whose work is bounded ([`NODE_BUDGET`]), from a greedy start that is its
//! floor when the budget runs out. Nothing is dropped: a job whose every candidate is blocked
//! takes the least blocked (ISSUE-798-a), and the plan's collision checks report what it still
//! crosses; what they cannot see is reported here (a leader back across its own view, one over
//! [`MAX_LEADER`], a search stopped at its budget).
//!
//! The length limits ([`MIN_LEADER`], [`MAX_LEADER`]), the clearances ([`CLEAR`], [`ROW`]) and
//! the inventory's caps are calibrated from draftwright's captured sheets, not taken from its
//! code: draftwright minimises length with no hard cap and no angle limit (none in `leaders.py`
//! or `_leader_candidates.py`), and its `leader_policy.py` is the region policy only (auto,
//! exterior, interior). Its side rule, the label on the side the leader runs to (`text_side`), is
//! ported; the port has no angle limit either.
//!
//! Interior candidates use the Analysis's projected edge evidence (ADR R1), shared with
//! `interior::Proof`: whole labels over blank face, shafts and arrowheads clear of visible
//! and hidden edges. The final inventory repair (`callout_routes`) compares these with the
//! exterior sweep only for crossing items, retaining the existing route on ties. GD&T frames
//! and surface labels share that sweep; datum frames retry only their existing face normal.
//! Feature footprints from the Analysis count crossings inside the box, shared with the
//! plan collision checks. The incomplete principal outer wires use solid bounds for re-entry
//! and distance to an exit; a filled projected material field is unavailable (ADR2-8). A
//! label's contacts include the arrowheads' flares, its own and the fixed ink's (ISSUE-367-a);
//! line contacts are tested by the shafts.

use std::cell::OnceCell;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BinaryHeap};

use super::PlannedDimension;
use super::footprints::Footprints;
use super::placement::{Frame, Page, Side, arrow, boxes_overlap, crosses, label_box};
use super::{CAP, P2, Placed, SHOULDER, Shape, Want, font, label_ink};
use crate::model::Problem;

/// The clearance an elbow keeps from the view's edge, or from the outermost dimension ink of the
/// strip it stands in, mm: one arrow and a little, so the leader has room for its head.
const CLEAR: f64 = 3.0;
/// The spacing of stacked callout rows, mm: a text height and two `pad_around_text`
/// (draftwright's `min_gap`).
const ROW: f64 = 7.0;
/// The shortest leader, mm: an arrowhead and about as much line again, so the leader reads as
/// one (draftwright's shortest on its captured sheets are 5.6 to 7 mm).
const MIN_LEADER: f64 = 6.0;
/// The longest leader taken while any shorter one is free of hard blockers, mm: a longer one
/// counts as one (draftwright's longest on its captured sheets is 73.5 mm, connector109's).
const MAX_LEADER: f64 = 75.0;
/// How far past [`MAX_LEADER`] a leader may measure and still meet it, mm. The sweep's longest
/// reach is the limit, and platforms' maths libraries round its `cos`, `sin` and `hypot`
/// differently in the last bit: compared exactly, the same route was legal on Apple Silicon and
/// too long on x86-64 Linux (#236).
const LIMIT_SLACK: f64 = 1e-6;

/// Whether a leader of *length* is longer than [`MAX_LEADER`], beyond rounding.
fn too_long(length: f64) -> bool {
    length > MAX_LEADER + LIMIT_SLACK
}

/// The least angle a leader to a turned step's silhouette makes with it, degrees: closer to
/// parallel, it reads as lying on the part's edge (ISO 128-22 has leaders meet the outline at an
/// angle; it states no figure).
const GRAZING: f64 = 15.0;
/// The step between candidate rows (beside a view) and columns (above and below it), mm.
const STEP: f64 = 1.0;
/// At most this many rows or columns are tried on each side of a view, however large the view.
const MAX_STATIONS: usize = 400;
/// The escapes' directions (draftwright's sixteen lead directions, `_END_DIAMETER_LEAD_DIRS`)
/// and how far past the view's edge their elbows go, mm.
const DIRECTIONS: usize = 16;
const ESCAPES: [f64; 3] = [CLEAR, 7.0, 11.0];
/// At most this many sites of one callout are tried (a pattern's members, nearest first).
const MAX_SITES: usize = 64;
/// The candidates each job keeps for the joint assignment, best first.
const KEEP: usize = 40;
/// A route escaping fixed ink also tries its tip turned by one lead direction either way
/// ([`ROTATIONS`]) and, per shoulder fraction ([`SHOULDER_STEPS`]), a shorter shelf on its side
/// and on the other: those and the incumbent join the job's [`KEEP`].
const ROTATIONS: [f64; 2] = [-1.0, 1.0];
const SHOULDER_STEPS: [f64; 3] = [0.25, 0.5, 0.75];
/// The joint assignment's work bound per group of conflicting jobs, in search nodes.
pub(super) const NODE_BUDGET: usize = 200_000;
/// The descent passes (single jobs, then conflicting pairs) that improve the greedy start before
/// the search.
const DESCENT: usize = 50;
/// The clearance a callout's label keeps from other labels, mm (draftwright stacks callout rows
/// a text height and two `pad_around_text` apart; half a pad keeps them visibly apart here).
const LABEL_PAD: f64 = 1.0;
/// Extra readable paper for compound fit/tolerance labels beside strokes and arrow flares.
pub(super) const STROKE_PAD: f64 = 2.0;

/// The first side shelf: full ink, elbow, shoulder, text gap and paper to the border.
pub(super) fn shelf_width(ink: &super::LabelInk) -> f64 {
    ink.max[0] - ink.min[0] + CLEAR + SHOULDER + 1.0 + STROKE_PAD
}

/// The weights of the objective's tiers, above any length in micrometres on a sheet.
/// Each tier's weight exceeds the most the tier below it reaches on one candidate.
const HARD: i64 = 1_000_000_000_000_000;
const CROSSING: i64 = 1_000_000_000_000;
/// The weight of ink outside the view's reserved area (its box and bands): below a crossing,
/// above any length (a metre, in micrometres).
const AREA: i64 = 1_000_000;
/// Between as many crossings, the fewer through text (draftwright protects text both ways,
/// `annotation_text_ink_clear`, and keeps shaft crossings under Policy B): above the area.
/// A candidate meets text of fewer than a thousand items, so this stays below one crossing.
const TEXT_INK: i64 = 1_000 * AREA;
/// A leader running along or close beside a centre stroke reads as part of it
/// ([`crate::centrelines::runs_beside`]): avoided at any cost short of one crossing.
const BESIDE: i64 = 100 * TEXT_INK;
/// A leader within [`GRAZING`] of the turned silhouette it points at reads as lying on the
/// part's edge. When a callout's best route grazes, every route but an angled one crossing no
/// more model line-work or features ([`Candidate::astray`]) costs this, as much as 20 mm more
/// ink (micrometres): worth a longer leader, never a crossing, and not worth sending the
/// neighbouring callouts far round to make room.
const GRAZE: i64 = 20_000;
/// The clear-route measurement (ISSUE-1187-a, after draftwright's test helper `_clear_routes`,
/// used by final inventory repair): from each of a callout's [`SWEEP_SITES`] sites nearest its leader's
/// tip, the leader leaves radially in [`SWEEP_DIRECTIONS`] directions, its elbow at each of
/// [`SWEEP_REACH`] from the rim, mm.
const SWEEP_DIRECTIONS: usize = 64;
const SWEEP_REACH: [f64; 10] = [6.0, 8.0, 11.0, 15.0, 20.0, 27.0, 36.0, 48.0, 62.0, 75.0];
const SWEEP_SITES: usize = 8;

/// One leader a callout could take.
#[derive(Clone, Debug)]
struct Candidate {
    tip: P2,
    elbow: P2,
    end: P2,
    label_at: P2,
    label_box: [f64; 4],
    /// The ink's length, micrometres.
    cost: i64,
    hard: u32,
    crossings: u32,
    furniture: u32,
    furniture_hard: u32,
    furniture_text: u32,
    /// Near strokes within the desired text clearance, after actual ink contacts.
    clearance: u32,
    /// Of the centre strokes it meets, those it runs along or close beside ([`BESIDE`]).
    beside: u32,
    /// Whether its shaft grazes the turned silhouette it points at (1) or not (0).
    grazing: u32,
    /// For a callout steered off its silhouette ([`inventory`]): 1 unless it does not graze
    /// and crosses no more of its view's model line-work, and no more features, than the
    /// grazing route ([`GRAZE`]); always 0 otherwise.
    astray: u32,
    feature_crossings: u32,
    recess: bool,
    /// Score the actual directed callout contacts in a shared recess solve.
    directed: bool,
    inside: i64,
    features_measured: bool,
    fixed_measured: bool,
    ignore_furniture: bool,
    aware: bool,
    /// Of its crossings, those that touch text: a line through a label, or two labels together.
    text: u32,
    /// Whether its ink leaves its view's reserved area (1) or not (0).
    outside: u32,
    /// The box of all its ink.
    bounds: [f64; 4],
    // Geometry is immutable; scores differ between the assignment variants.
    conflict_ink: OnceCell<Box<ConflictInk>>,
}

#[derive(Clone, Debug)]
struct ConflictInk {
    bounds: [f64; 4],
    label: [f64; 4],
    segments: [[P2; 2]; 2],
    lines: [Line; 2],
}

fn padded(b: [f64; 4]) -> [f64; 4] {
    [
        b[0] - LABEL_PAD,
        b[1] - LABEL_PAD,
        b[2] + LABEL_PAD,
        b[3] + LABEL_PAD,
    ]
}

impl Candidate {
    fn conflict_ink(&self) -> &ConflictInk {
        self.conflict_ink.get_or_init(|| {
            let segments = self.segments();
            Box::new(ConflictInk {
                bounds: padded(self.bounds),
                label: padded(self.label_box),
                segments,
                lines: segments.map(Line::new),
            })
        })
    }

    fn measure_features(&mut self, job: &Job) {
        if !self.features_measured {
            let shaft = [self.tip, self.elbow];
            self.feature_crossings = job.footprints.crossing_count(shaft) as u32;
            self.features_measured = true;
        }
    }

    fn without_furniture(&mut self) {
        self.hard -= self.furniture_hard;
        self.crossings -= self.furniture_text;
        self.text -= self.furniture_text;
        self.furniture = 0;
        self.furniture_hard = 0;
        self.furniture_text = 0;
        self.beside = 0;
    }

    fn segments(&self) -> [[P2; 2]; 2] {
        [[self.tip, self.elbow], [self.elbow, self.end]]
    }

    fn score(&self) -> i64 {
        i64::from(self.hard) * HARD
            + if self.aware {
                i64::from(
                    self.crossings
                        + self.outside
                        + self.feature_crossings * if self.recess { 2 } else { 1 },
                ) * CROSSING
                    + i64::from(self.text) * TEXT_INK
                    + self.cost
                    + self.inside
            } else {
                i64::from(self.crossings) * CROSSING
                    + i64::from(self.text) * TEXT_INK
                    + i64::from(self.outside) * AREA
                    + self.cost
            }
            + i64::from(self.clearance) * (TEXT_INK / 10)
            + i64::from(self.furniture) * 100_000
            + i64::from(self.beside) * BESIDE
            + i64::from(self.astray) * GRAZE
    }

    /// Whether it is clear: no hard blocker and nothing crossed.
    fn clear(&self) -> bool {
        self.hard == 0
            && self.crossings == 0
            && self.furniture == 0
            && self.clearance == 0
            && (!self.aware || self.feature_crossings == 0)
    }

    fn placed(&self) -> Placed {
        Placed {
            lines: vec![[self.tip, self.elbow], [self.elbow, self.end]],
            arrows: vec![arrow(self.tip, self.elbow)],
            label_at: self.label_at,
            rotation: 0.0,
            label_box: self.label_box,
        }
    }
}

/// What the callouts must keep clear of besides the page: the placed dimensions' lines (with
/// their arrowheads' shafts) and labels, each with the key of the item it belongs to, as the
/// plan's collision checks report one problem per pair of items.
struct Fixed {
    segments: Vec<(usize, [P2; 2])>,
    labels: Vec<(usize, [f64; 4])>,
    extensions: Vec<(usize, [P2; 2])>,
    extension_index: ExtensionIndex,
    /// The arrowheads' outlines as `dims::render` draws them ([`head`]), which must stay out of
    /// the label: a flare can reach into it beside a shaft that does not.
    heads: Vec<(usize, [P2; 2])>,
    /// Where each of them lies, so a candidate tests only those near it.
    grid: Grid,
    furniture: BTreeMap<usize, bool>,
    /// Of the furniture, the centre marks' crosses.
    crosses: std::collections::BTreeSet<usize>,
}

impl Fixed {
    /// The fixed ink of the placed items *on* (each with its key).
    fn new<'p>(on: impl Iterator<Item = (usize, &'p Placed)> + Clone) -> Fixed {
        let segments: Vec<(usize, [P2; 2])> = on
            .clone()
            .flat_map(|(i, p)| p.lines.iter().chain(&p.arrows).map(move |s| (i, *s)))
            .collect();
        let labels: Vec<(usize, [f64; 4])> = on.clone().map(|(i, p)| (i, p.label_box)).collect();
        let extensions: Vec<_> = on
            .clone()
            .filter(|(_, p)| p.arrows.len() == 2 && p.lines.len() >= 3)
            .flat_map(|(i, p)| p.lines[..2].iter().map(move |s| (i, *s)))
            .collect();
        let heads: Vec<(usize, [P2; 2])> = on
            .flat_map(|(i, p)| p.arrows.iter().flat_map(move |a| head(*a).map(|e| (i, e))))
            .collect();
        let line_box = |s: &(usize, [P2; 2])| {
            let [a, b] = s.1;
            [
                a[0].min(b[0]),
                a[1].min(b[1]),
                a[0].max(b[0]),
                a[1].max(b[1]),
            ]
        };
        let grid = Grid::new([
            segments.iter().map(line_box).collect(),
            labels.iter().map(|l| l.1).collect(),
            heads.iter().map(line_box).collect(),
        ]);
        let extension_index = ExtensionIndex::new(extensions.clone());
        Fixed {
            segments,
            labels,
            extensions,
            extension_index,
            heads,
            grid,
            furniture: BTreeMap::new(),
            crosses: Default::default(),
        }
    }
}

// An owner may have two witnesses. Dense inventories usually fit in one
// integer; unusual larger owner IDs retain the general set semantics.
#[derive(Default)]
struct ContactOwners {
    small: u128,
    large: std::collections::BTreeSet<usize>,
}
impl ContactOwners {
    fn insert(&mut self, owner: usize) {
        if owner < 128 {
            self.small |= 1u128 << owner;
        } else {
            self.large.insert(owner);
        }
    }
    fn len(&self) -> usize {
        self.small.count_ones() as usize + self.large.len()
    }
}

struct ExtensionIndex {
    ink: Vec<(usize, [P2; 2])>,
    bounds: Vec<[f64; 4]>,
    grid: Option<Grid>,
}
impl ExtensionIndex {
    fn new(ink: Vec<(usize, [P2; 2])>) -> Self {
        let bounds: Vec<_> = ink
            .iter()
            .map(|(_, e)| grow(segment_bounds(*e), LABEL_PAD))
            .collect();
        let grid = (ink.len() > 64).then(|| Grid::new([bounds.clone(), Vec::new(), Vec::new()]));
        Self { ink, bounds, grid }
    }
    /// Count physical witness strokes touched by the arrow flare (not distinct owners).
    /// The contact grid's 1 mm boxes conservatively include the exact 0.2 mm test.
    fn head_contacts(&self, flare: &[[P2; 2]]) -> usize {
        let reach = flare.iter().map(|s| segment_bounds(*s)).fold(
            [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ],
            |a, b| {
                [
                    a[0].min(b[0]),
                    a[1].min(b[1]),
                    a[2].max(b[2]),
                    a[3].max(b[3]),
                ]
            },
        );
        let mut count = 0;
        let mut measure = |i: usize| {
            let bounds = grow(segment_bounds(self.ink[i].1), 0.2);
            count += usize::from(flare.iter().any(|s| touches_box(*s, bounds)));
        };
        if let Some(grid) = &self.grid {
            grid.near(0, reach, measure);
        } else {
            for (i, b) in self.bounds.iter().enumerate() {
                if boxes_meet(*b, reach) {
                    measure(i);
                }
            }
        }
        count
    }

    fn contacts(&self, lines: &[[P2; 2]], label: [f64; 4]) -> (usize, usize) {
        self.contacts_bounded(lines, label, None)
            // Invariant: The counter returns None only for a supplied bound; this call supplies none.
            .expect("unbounded contacts")
    }

    fn contacts_bounded(
        &self,
        lines: &[[P2; 2]],
        label: [f64; 4],
        maximum: Option<(usize, usize)>,
    ) -> Option<(usize, usize)> {
        let pad = grow(label, LABEL_PAD);
        let reach = lines.iter().flatten().fold(pad, |b, p| {
            [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ]
        });
        let (mut pairs, mut text) = (ContactOwners::default(), ContactOwners::default());
        let mut measure = |n: usize| {
            let (owner, e) = self.ink[n];
            let on_text = touches_box(e, pad);
            if on_text {
                text.insert(owner);
            }
            if on_text || lines.iter().any(|s| touches_box(*s, self.bounds[n])) {
                pairs.insert(owner);
            }
            maximum.is_none_or(|(p, t)| pairs.len() <= p && text.len() <= t)
        };
        let complete = if let Some(grid) = &self.grid {
            grid.try_near(0, reach, measure)
        } else {
            self.bounds
                .iter()
                .enumerate()
                .filter(|(_, b)| boxes_meet(**b, reach))
                .all(|(n, _)| measure(n))
        };
        complete.then(|| (pairs.len(), text.len()))
    }
}

/// A uniform grid over boxes of three kinds (the fixed segments, labels and heads): each cell
/// lists the boxes that meet it, within a hair ([`touches`]), so the boxes a query box meets are
/// among those its cells list (each given once, with some that do not meet it). A box off the
/// grid lies in its edge cells, as does a query. A box that is not finite is listed for every
/// query, and a query that is not finite is given every box, so nothing the tests could pass is
/// left out.
struct Grid {
    origin: P2,
    cell: f64,
    size: [usize; 2],
    cells: [Vec<Vec<u32>>; 3],
    always: [Vec<u32>; 3],
    count: [usize; 3],
    /// The last query that gave each box, so a box listed in several cells is given once.
    seen: [Vec<std::cell::Cell<u64>>; 3],
    query: std::cell::Cell<u64>,
}

fn finite(b: [f64; 4]) -> bool {
    b.iter().all(|v| v.is_finite())
}

/// The grid's cells along its longer side.
const GRID: f64 = 32.0;

impl Grid {
    fn new(boxes: [Vec<[f64; 4]>; 3]) -> Grid {
        let all = boxes.iter().flatten().filter(|b| finite(**b));
        let lo = all
            .clone()
            .fold([f64::INFINITY; 2], |m, b| [m[0].min(b[0]), m[1].min(b[1])]);
        let hi = all.fold([f64::NEG_INFINITY; 2], |m, b| {
            [m[0].max(b[2]), m[1].max(b[3])]
        });
        let (origin, extent) = if lo[0] <= hi[0] && lo[1] <= hi[1] {
            (lo, (hi[0] - lo[0]).max(hi[1] - lo[1]))
        } else {
            ([0.0; 2], 0.0)
        };
        let cell = (extent / GRID).max(1.0);
        let size = [
            ((hi[0] - origin[0]) / cell).floor().clamp(0.0, GRID) as usize + 1,
            ((hi[1] - origin[1]) / cell).floor().clamp(0.0, GRID) as usize + 1,
        ];
        let mut grid = Grid {
            origin,
            cell,
            size,
            cells: std::array::from_fn(|_| vec![Vec::new(); size[0] * size[1]]),
            always: Default::default(),
            count: std::array::from_fn(|k| boxes[k].len()),
            seen: std::array::from_fn(|k| vec![std::cell::Cell::new(0); boxes[k].len()]),
            query: std::cell::Cell::new(0),
        };
        for (kind, list) in boxes.iter().enumerate() {
            for (n, b) in list.iter().enumerate() {
                if !finite(*b) {
                    grid.always[kind].push(n as u32);
                    continue;
                }
                let (xs, ys) = grid.span(*b);
                for y in ys {
                    for x in xs.clone() {
                        grid.cells[kind][y * size[0] + x].push(n as u32);
                    }
                }
            }
        }
        grid
    }

    /// The columns and rows of the cells box *b* (grown by a hair) meets, clamped to the grid. A
    /// box given with its corners swapped is taken as the box they span, which holds every point
    /// the tests could find it meeting.
    fn span(
        &self,
        b: [f64; 4],
    ) -> (
        std::ops::RangeInclusive<usize>,
        std::ops::RangeInclusive<usize>,
    ) {
        const HAIR: f64 = 1e-6;
        let at = |v: f64, o: f64, n: usize| {
            ((v - o) / self.cell).floor().clamp(0.0, (n - 1) as f64) as usize
        };
        let (o, [nx, ny]) = (self.origin, self.size);
        let (x0, x1) = (b[0].min(b[2]), b[0].max(b[2]));
        let (y0, y1) = (b[1].min(b[3]), b[1].max(b[3]));
        (
            at(x0 - HAIR, o[0], nx)..=at(x1 + HAIR, o[0], nx),
            at(y0 - HAIR, o[1], ny)..=at(y1 + HAIR, o[1], ny),
        )
    }

    fn try_near(&self, kind: usize, b: [f64; 4], mut f: impl FnMut(usize) -> bool) -> bool {
        if !finite(b) {
            return (0..self.count[kind]).all(f);
        }
        let query = self.query.get() + 1;
        self.query.set(query);
        let seen = &self.seen[kind];
        for &n in &self.always[kind] {
            if !f(n as usize) {
                return false;
            }
        }
        let (xs, ys) = self.span(b);
        for y in ys {
            for x in xs.clone() {
                for &n in &self.cells[kind][y * self.size[0] + x] {
                    if seen[n as usize].replace(query) != query && !f(n as usize) {
                        return false;
                    }
                }
            }
        }
        true
    }

    /// Calls *f* with the index of each box of *kind* listed in the cells *b* meets, once.
    fn near(&self, kind: usize, b: [f64; 4], mut f: impl FnMut(usize)) {
        if !finite(b) {
            (0..self.count[kind]).for_each(f);
            return;
        }
        let query = self.query.get() + 1;
        self.query.set(query);
        let seen = &self.seen[kind];
        self.always[kind].iter().for_each(|&n| f(n as usize));
        let (xs, ys) = self.span(b);
        for y in ys {
            for x in xs.clone() {
                for &n in &self.cells[kind][y * self.size[0] + x] {
                    if seen[n as usize].replace(query) != query {
                        f(n as usize);
                    }
                }
            }
        }
    }
}

/// The three edges of the arrowhead on *arrow* (tip, tail), as `dims::render` draws it.
pub(super) fn head(arrow: [P2; 2]) -> [[P2; 2]; 3] {
    let [tip, tail] = arrow;
    let n = [tail[1] - tip[1], tip[0] - tail[0]];
    let k = super::ARROW_HALF / n[0].hypot(n[1]).max(1e-12);
    let (a, b) = (
        [tail[0] + k * n[0], tail[1] + k * n[1]],
        [tail[0] - k * n[0], tail[1] - k * n[1]],
    );
    [[tip, a], [a, b], [b, tip]]
}

fn boss_profile(w: &Want) -> bool {
    w.views.len() > 1
        && !w.measurements.is_empty()
        && w.measurements
            .iter()
            .all(|m| m.parameter == "boss.diameter")
        && matches!(&w.shape, Shape::Callout { sites, .. } if !sites.is_empty() && sites.iter().all(|(_, r)| *r == 0.0))
}

/// A slot callout (`N× SLOT W × L`, `dims::slots`): its leader may land on any of its slots, so
/// a feature its leader crosses weighs double, as a recess callout's does, so the leader reaches
/// another of them rather than crossing a neighbouring feature to the nearest (FTC-09's slot 3).
fn slot_callout(w: &Want) -> bool {
    matches!(w.shape, Shape::Callout { .. }) && w.terms.get(1).is_some_and(|t| t == "SLOT")
}

type Site = (P2, f64, Option<crate::arc::Span>);

/// One callout's job: its view's outline, its sites on the page (centre, radius), its label's ink
/// box about its baseline origin, and how far past each edge the strip's dimensions reach.
#[derive(Clone)]
struct Job<'a> {
    side: Option<crate::placement_intent::Side>,
    view: &'a str,
    footprints: &'a Footprints,
    outline: [f64; 4],
    /// The view's reserved area (box and bands), when the sheet has it.
    area: Option<[f64; 4]>,
    sites: Vec<Site>,
    radial: bool,
    /// For a turned step's diameter, whose sites lie on its silhouette: the silhouette's
    /// direction on the page ([`grazing`]).
    silhouette: Option<P2>,
    /// For such a callout, its view's model line-work ([`view_ink`]): a route across it is
    /// what an angle off the silhouette may not buy ([`Candidate::astray`]).
    model: Option<&'a SegmentIndex>,
    corridors: bool,
    /// A slot callout ([`slot_callout`]): a crossed feature weighs double.
    slot: bool,
    directed: bool,
    short_profile: bool,
    inward: bool,
    edge_attachment: bool,
    interior_proof: Option<(&'a crate::model::PlannedView, &'a super::interior::Proof)>,
    profile_proof: Option<(&'a crate::model::PlannedView, &'a super::interior::Proof)>,
    /// Once [`inventory`] steers the callout off its silhouette: the most model lines and
    /// features an angled route may cross, the grazing route's ([`astray`]).
    steer: std::cell::Cell<Option<(usize, usize)>>,
    ink_min: P2,
    ink_max: P2,
    stroke_pad: f64,
    /// The page radius of the callout's own outermost circle (its counterbore, spotface or
    /// countersink, else its bore), [`own_reach`]: a centre mark past its overrun is shared.
    reach: f64,
    /// Below, above, left, right: the distance past that edge clear of the strip's dimensions.
    beyond: [f64; 4],
}

/// The page radius of a callout's own outermost circle: the largest diameter it states (a
/// counterbore, spotface or countersink round its bore), at the view's *scale*.
fn own_reach(measurements: &[super::Measurement], scale: f64) -> f64 {
    measurements
        .iter()
        .filter(|m| m.parameter.ends_with(".diameter"))
        .map(|m| m.value / 2. * scale)
        .fold(0., f64::max)
}

/// Compound requirements need paper around their extra signs and smaller stacked figures.
/// Ordinary labels retain their established corridors; all labels still test actual strokes.
fn stroke_clearance(terms: &[String]) -> f64 {
    let qualified = terms
        .iter()
        .flat_map(|t| t.split(|c: char| c.is_whitespace() || matches!(c, '(' | ')')))
        .any(|word| {
            word == "-"
                || word
                    .strip_prefix('±')
                    .is_some_and(|n| n.parse::<f64>().is_ok())
                || (word.starts_with(['+', '-']) && word.parse::<f64>().is_ok())
                || super::tolerance::fit(word).is_ok()
        });
    if qualified { STROKE_PAD } else { 0.0 }
}

fn side_index(side: Side) -> usize {
    match side {
        Side::Below => 0,
        Side::Above => 1,
        Side::Left => 2,
        Side::Right => 3,
    }
}

/// Routing needs only this immutable classification. Lowering a dense AP242
/// document again for every callout discards identical items and diagnostics.
pub(super) fn has_semantic_pmi(
    analysis: &crate::model::Analysis,
    frames: &BTreeMap<&str, Frame>,
) -> bool {
    let key = ["front", "plan", "side"]
        .map(|name| u8::from(frames.contains_key(name)))
        .to_vec();
    crate::planning::memo(analysis, "leader-semantic-pmi", key, || {
        !crate::pmi_lowering::lower(analysis, &|name| frames.contains_key(name))
            .0
            .is_empty()
    })
}

/// Places every callout of *items* (its view in *views_of*), whose entry in *placed* is empty,
/// around its view, clear of the dimensions already in *placed* (those with a strip in *sides*)
/// as far as it can be; see the module's notes. Every item the part would have is placed, so
/// where a callout goes does not depend on what the spec omits (ARCH0003-2).
///
/// Returns the problems the plan's collision checks cannot see: a callout that kept a leader
/// back across its own view or longer than [`MAX_LEADER`] (every candidate it had was blocked),
/// and a group of callouts whose search stopped at [`NODE_BUDGET`] (its assignment is the best
/// found, not proved the best).
#[allow(clippy::too_many_arguments)]
pub(super) fn place(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    cache: &super::footprints::Cache,
    views: &[crate::model::PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    sides: &[Option<Side>],
    placed: &mut [Option<Placed>],
    page: &Page,
) -> Vec<Problem> {
    place_with_obstacles(
        analysis,
        spec,
        cache,
        views,
        frames,
        items,
        views_of,
        sides,
        placed,
        page,
        &[],
    )
}

pub(crate) fn default_nominal_intent(spec: &crate::model::Spec) -> bool {
    spec.dimension_schemes.is_empty()
        && spec.features.values().all(|f| *f == Default::default())
        && spec
            .annotations
            .values()
            .all(|a| a.text.is_none() && a.place.as_ref().is_none_or(|p| *p == Default::default()))
        && spec.dimensions.values().all(|d| {
            let mut d = d.clone();
            d.omit = false;
            if d.place.as_ref().is_some_and(|p| {
                *p == Default::default() || crate::placement_intent::automatic_pin(p)
            }) {
                d.place = None;
            }
            d == Default::default()
        })
}

pub(super) fn nominal_context(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    items: &[Want],
) -> bool {
    crate::nominal::geometry_eligible(analysis)
        && !crate::nominal::comparison()
        && default_nominal_intent(spec)
        && spec.precision.is_empty()
        && !crate::placement_intent::has_pins(spec)
        && items.iter().flat_map(|w| &w.measurements).any(|m| {
            crate::nominal::format(m.value)
                .split_once('.')
                .is_some_and(|(_, fraction)| fraction.len() > 1)
        })
}

fn nominal_reference(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    items: &[Want],
) -> Option<crate::model::Plan> {
    if !nominal_context(analysis, spec, items) {
        return None;
    }
    if !crate::dims::has_new_native_recess_dimensions(analysis) {
        return None;
    }
    let reference_key = crate::model::established_key(spec);
    crate::planning::lookup::<Result<crate::model::Plan, crate::model::Invalid>>(
        analysis,
        "established-drawing",
        &reference_key,
    )
    .and_then(Result::ok)
}

#[derive(Clone)]
struct NominalReferences {
    full: crate::model::Plan,
    legacy: Option<crate::model::Plan>,
}

// A late comparison cannot influence view selection, lane ownership or sheet search.
// It contributes remeasured alternatives only after the complete inventory has settled.
fn settled_nominal_reference(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    sheet: &crate::model::PlannedSheet,
    items: &[PlannedDimension],
) -> Option<NominalReferences> {
    if crate::nominal::comparison()
        || !crate::nominal::geometry_eligible(analysis)
        || !default_nominal_intent(spec)
        || !spec.precision.is_empty()
        || (crate::placement_intent::has_pins(spec)
            && !crate::placement_intent::automatic_pins_only(spec))
        || !spec.views.values().all(|v| *v == Default::default())
        || !spec.faces.is_empty()
        || !spec.notes.is_empty()
        || !spec.tables.is_empty()
        || spec.sheets.as_ref().is_some_and(|s| {
            let mut policy = s.clone();
            policy.max = None;
            policy != Default::default()
        })
        || spec.iso_scale_factor.is_some()
        || spec.envelope_tolerance.is_some()
        || !spec.gears.is_empty()
        || !spec.feature_declarations.is_empty()
    {
        return None;
    }
    let legacy_key = crate::model::established_key(spec);
    let legacy = crate::planning::lookup::<Result<crate::model::Plan, crate::model::Invalid>>(
        analysis,
        "established-drawing",
        &legacy_key,
    )
    .and_then(Result::ok);
    let unchanged_fractional = items
        .iter()
        .filter(|d| {
            d.kind == "callout"
                && d.placed.is_some()
                && !d
                    .measurements
                    .iter()
                    .any(|m| m.feature.starts_with("f:section_recess:"))
        })
        .any(|d| {
            d.measurements.iter().any(|m| {
                let n = crate::nominal::format(m.value);
                n.split_once('.').is_some_and(|(_, tail)| tail.len() == 1)
            })
        })
        && items
            .iter()
            .filter(|d| d.kind == "callout" && d.placed.is_some())
            .all(|d| {
                d.measurements.iter().all(|m| {
                    crate::nominal::format(m.value)
                        .split_once('.')
                        .is_none_or(|(_, tail)| tail.len() <= 1)
                })
            });
    let recess_statement = items.iter().any(|d| {
        d.kind == "callout"
            && d.placed.is_some()
            && d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
    });
    if let Some(full) = legacy.as_ref().filter(|r| {
        recess_statement && r.sheet.scale_factor == sheet.scale_factor && !unchanged_fractional
    }) {
        return Some(NominalReferences {
            full: full.clone(),
            legacy: Some(full.clone()),
        });
    }
    let extensions: Vec<_> = items
        .iter()
        .enumerate()
        .filter(|(_, d)| witnessed(d))
        .flat_map(|(i, d)| {
            d.placed
                .iter()
                .flat_map(move |p| p.lines.iter().take(2).map(move |s| (i, *s)))
        })
        .collect();
    // An established bore can need its canonical route budget even when
    // its text stayed integral; a displaced visible-rim recipe is not a budget.
    let canonical_bores = analysis
        .edge_ink
        .as_ref()
        .is_some_and(|ink| !ink.resolved.is_empty())
        && items.iter().filter(|d| d.kind == "callout").any(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "bore.diameter")
                && d.measurements.iter().all(|m| {
                    crate::nominal::format(m.value)
                        .split_once('.')
                        .is_none_or(|(_, tail)| tail.len() <= 1)
                })
                && d.placed
                    .as_ref()
                    .is_some_and(|p| length(&p.lines) > MIN_LEADER + SHOULDER)
        });
    let needs_reference = items.iter().filter(|d| d.kind == "callout").any(|d| {
        let Some(p) = &d.placed else { return false };
        if d.measurements
            .iter()
            .all(|m| m.feature.starts_with("f:section_recess:"))
        {
            return false;
        }
        let grew = d.measurements.iter().any(|m| {
            crate::nominal::format(m.value)
                .split_once('.')
                .is_some_and(|(_, tail)| tail.len() > 1)
        });
        let contact = extension_contacts(&extensions, &p.lines, p.label_box).1 > 0
            || extensions.iter().any(|(_, e)| {
                head(p.arrows[0])
                    .iter()
                    .any(|h| touches_box(*h, grow(segment_bounds(*e), 0.2)))
            });
        (contact
            && d.measurements
                .iter()
                .any(|m| crate::nominal::format(m.value).contains('.')))
            || (grew && length(&p.lines) > MIN_LEADER + SHOULDER)
    });
    if !needs_reference && !canonical_bores && legacy.is_none() {
        return None;
    }
    let recovered_recipe =
        canonical_bores || unchanged_fractional || (legacy.is_some() && !recess_statement);
    let recipe = crate::model::Spec {
        hole_table: spec.hole_table,
        sheet: crate::model::SheetSpec {
            size: (!recovered_recipe).then(|| sheet.size.clone()),
            scale: (!recovered_recipe).then(|| crate::model::scale_name(sheet.scale_factor)),
            projection: spec.sheet.projection.clone(),
            references: spec.sheet.references,
            ..Default::default()
        },
        ..Default::default()
    };
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    let key = serde_json::to_vec(&recipe).expect("reference intent serializes");
    // An unrendered comparison drawing: normal spacing, whatever the candidate's spacing.
    let full = crate::dims::with_compact(false, || {
        crate::planning::memo(analysis, "selected-nominal-reference", key, || {
            crate::nominal::with_comparison(|| {
                // New notch statements cannot displace established circular
                // callouts in the very reference that bounds their final routes.
                // Only this unrendered comparison omits the added inventory.
                let reference = analysis
                    .features
                    .iter()
                    .any(|f| f.kind == "rectangular_through_notch")
                    .then(|| {
                        let mut a = analysis.clone();
                        a.features.retain(|f| f.kind != "rectangular_through_notch");
                        a
                    });
                let analysis = reference.as_ref().unwrap_or(analysis);
                crate::planning::isolated(analysis, || {
                    crate::planning::store(
                        analysis,
                        "canonical-circular-recipes",
                        Vec::new(),
                        true,
                    );
                    if recovered_recipe {
                        crate::model::plan_automatic(analysis, &recipe).ok()
                    } else {
                        crate::model::plan_escalated(analysis, &recipe).ok()
                    }
                })
            })
        })
    })?;
    let legacy_key = crate::model::established_key(spec);
    let legacy = crate::planning::lookup::<Result<crate::model::Plan, crate::model::Invalid>>(
        analysis,
        "established-drawing",
        &legacy_key,
    )
    .and_then(Result::ok);
    Some(NominalReferences { full, legacy })
}

#[allow(clippy::too_many_arguments)]
pub(super) fn place_with_obstacles(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    cache: &super::footprints::Cache,
    views: &[crate::model::PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    sides: &[Option<Side>],
    placed: &mut [Option<Placed>],
    page: &Page,
    obstacles: &[PlannedDimension],
) -> Vec<Problem> {
    if !items.iter().enumerate().any(|(i, w)| {
        matches!(w.shape, Shape::Callout { .. }) && views_of[i].is_some() && placed[i].is_none()
    }) {
        return Vec::new();
    }
    let reference = nominal_reference(analysis, spec, items);
    let route_policy =
        crate::planning::lookup::<bool>(analysis, "established-route-policy", &[]).unwrap_or(false);
    let frame_geometry: Vec<_> = frames
        .iter()
        .map(|(name, f)| (name, f.memo_geometry()))
        .collect();
    let canonical = crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
        .unwrap_or(false);
    let key = serde_json::to_vec(&(
        &spec.dimensions,
        views,
        frame_geometry,
        canonical,
        items,
        views_of,
        sides,
        &*placed,
        page,
        obstacles,
        route_policy,
        crate::nominal::comparison(),
        &reference,
    ))
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("leader solve inputs serialize");
    let (next, problems) = crate::planning::memo(analysis, "settled-leader-solve", key, || {
        let mut next = placed.to_vec();
        let problems = place_with_obstacles_uncached(
            analysis,
            spec,
            cache,
            views,
            frames,
            items,
            views_of,
            sides,
            &mut next,
            page,
            obstacles,
            reference.as_ref(),
        );
        (next, problems)
    });
    placed.clone_from_slice(&next);
    problems
}

#[allow(clippy::too_many_arguments)]
fn place_with_obstacles_uncached(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    cache: &super::footprints::Cache,
    views: &[crate::model::PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    sides: &[Option<Side>],
    placed: &mut [Option<Placed>],
    page: &Page,
    obstacles: &[PlannedDimension],
    reference: Option<&crate::model::Plan>,
) -> Vec<Problem> {
    let on = || {
        placed
            .iter()
            .enumerate()
            .filter_map(|(i, p)| Some((i, p.as_ref()?)))
    };
    let mut fixed = Fixed::new(
        on().chain(
            obstacles
                .iter()
                .enumerate()
                .filter_map(|(i, d)| Some((items.len() + i, d.placed.as_ref()?))),
        ),
    );
    fixed.furniture = obstacles
        .iter()
        .enumerate()
        .filter(|(_, d)| d.kind == "ink")
        .map(|(i, d)| (items.len() + i, d.basis == "global_axis"))
        .collect();
    fixed.crosses = obstacles
        .iter()
        .enumerate()
        .filter(|(_, d)| d.kind == "ink" && d.basis == "cross")
        .map(|(i, _)| items.len() + i)
        .collect();
    // How far each view's strips reach with dimension ink, past the view's edges.
    let mut reach: BTreeMap<(&str, usize), f64> = BTreeMap::new();
    for i in 0..items.len() {
        let (Some(view), Some(side), Some(p)) = (views_of[i], sides[i], placed[i].as_ref()) else {
            continue;
        };
        let o = frames[view].outline();
        let mut far = 0.0f64;
        for q in super::placement::ink_points(p) {
            far = far.max(match side {
                Side::Below => o[1] - q[1],
                Side::Above => q[1] - o[3],
                Side::Left => o[0] - q[0],
                Side::Right => q[0] - o[2],
            });
        }
        let e = reach.entry((view, side_index(side))).or_insert(0.0);
        *e = e.max(far);
    }
    let footprints: BTreeMap<_, _> = views
        .iter()
        .map(|v| (v.id.trim_start_matches("v:"), cache.get(analysis, v)))
        .collect();
    // Include model ink for recesses and bevel/notch-displaced radius leaders.
    let bounded_notches = !crate::notches::requirements(analysis).is_empty();
    let recess_context = items.iter().any(|w| {
        w.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"))
    });
    let models: BTreeMap<_, _> = views
        .iter()
        .filter(|v| {
            let id = v.id.trim_start_matches("v:");
            items.iter().enumerate().any(|(i, w)| {
                views_of[i] == Some(id)
                    && placed[i].is_none()
                    && (frames.get(id).is_some_and(|f| grazing(f, w).is_some())
                        || (bounded_notches
                            && w.measurements.iter().any(|m| m.parameter == "blend.radius"))
                        || w.measurements
                            .iter()
                            .any(|m| m.feature.starts_with("f:section_recess:")))
            })
        })
        .filter_map(|v| Some((v.id.trim_start_matches("v:"), cache.model(analysis, v)?)))
        .collect();
    // Semantic PMI uses the legacy floor; its unused footprint-aware baseline need not be
    // sorted. The furniture-aware inventory and the legacy survivors stay fully measured.
    let semantic_pmi = has_semantic_pmi(analysis, frames);
    let profile_proofs = if items.iter().any(boss_profile) {
        super::interior::proofs(analysis, views)
    } else {
        BTreeMap::new()
    };
    let mut jobs: Vec<(usize, Job)> = Vec::new();
    for (i, w) in items.iter().enumerate() {
        let (Shape::Callout { sites, arcs }, Some(view)) = (&w.shape, views_of[i]) else {
            continue;
        };
        if placed[i].is_some() {
            continue;
        }
        let f = &frames[view];
        let ink = label_ink(&w.terms, font());
        let beyond = [0, 1, 2, 3].map(|k| {
            reach
                .get(&(view, k))
                .map_or(CLEAR, |r| if *r > 0.0 { r + CLEAR } else { CLEAR })
        });
        jobs.push((
            i,
            Job {
                side: spec
                    .dimensions
                    .get(&w.id)
                    .and_then(|d| d.place.as_ref())
                    .and_then(crate::placement_intent::side_hint),
                view,
                footprints: &footprints[view],
                outline: f.outline(),
                area: page.area(view),
                sites: page_sites(f, sites, arcs),
                radial: !crate::nominal::comparison()
                    && w.measurements.iter().any(|m| {
                        m.parameter == "slot_end_radius.radius"
                            && crate::nominal::format(m.value)
                                .split_once('.')
                                .is_some_and(|(_, tail)| tail.len() > 1)
                    }),
                silhouette: grazing(f, w),
                model: models.get(view).map(|m| &m.as_ref().0),
                short_profile: boss_profile(w),
                inward: false,
                edge_attachment: true,
                interior_proof: None,
                profile_proof: boss_profile(w)
                    .then(|| {
                        let v = views
                            .iter()
                            .find(|v| v.id.trim_start_matches("v:") == view)?;
                        Some((v, profile_proofs.get(&v.id)?.as_ref()))
                    })
                    .flatten(),
                directed: recess_context
                    && !semantic_pmi
                    && !crate::planning::lookup::<bool>(analysis, "established-route-policy", &[])
                        .unwrap_or(false),
                slot: slot_callout(w),
                corridors: boss_profile(w)
                    || w.measurements
                        .iter()
                        .any(|m| m.feature.starts_with("f:section_recess:")),
                steer: std::cell::Cell::new(None),
                ink_min: ink.min,
                ink_max: ink.max,
                stroke_pad: if bounded_notches
                    && w.measurements.iter().any(|m| m.parameter == "blend.radius")
                {
                    stroke_clearance(&w.terms).max(LABEL_PAD)
                } else {
                    stroke_clearance(&w.terms)
                },
                reach: own_reach(&w.measurements, f.scale()),
                beyond,
            },
        ));
    }
    let measured: Vec<_> = jobs
        .iter()
        .map(|(i, job)| {
            inventory(
                job,
                &fixed,
                page,
                semantic_pmi,
                bounded_notches
                    && job.stroke_pad > 0.0
                    && items[*i]
                        .measurements
                        .iter()
                        .any(|m| m.parameter == "polygon_across_flats.length"),
            )
        })
        .collect();
    let mut inventories: Vec<_> = measured.iter().map(|v| v.0.clone()).collect();
    let mut baseline: Vec<_> = measured.iter().map(|v| v.1.clone()).collect();
    let mut baseline_original: Vec<_> = measured.iter().map(|v| v.2.clone()).collect();
    if let Some(reference) = reference {
        for (k, (i, job)) in jobs.iter().enumerate() {
            let w = &items[*i];
            let Some(old) = reference.dimensions.iter().find(|d| {
                d.id == w.id && d.measurements.iter().all(|m| w.measurements.contains(m))
            }) else {
                continue;
            };
            let Some(p) = nominal_route(reference, old, views, w) else {
                continue;
            };
            if p.lines.len() != 2 {
                continue;
            }
            let [tip, elbow] = p.lines[0];
            let end = p.lines[1][1];
            if let Some(c) = measure_at(job, tip, elbow, end, end[0] >= elbow[0], &fixed, page) {
                inventories[k].push(c.clone());
                baseline[k].push(c.clone());
                baseline_original[k].push(c);
                for inventory in [
                    &mut inventories[k],
                    &mut baseline[k],
                    &mut baseline_original[k],
                ] {
                    inventory.sort_by_key(Candidate::score);
                }
            }
        }
    }
    // Semantic PMI claims its strips after dimensions. Until those strips participate in the
    // joint solve, retain the established objective rather than planning PMI twice as a probe.
    let defects = |inv: &[Vec<Candidate>], selected: &[usize]| {
        let mut count = 0;
        for (k, &c) in selected.iter().enumerate() {
            let a = &inv[k][c];
            count += a.hard - a.furniture_hard + a.crossings - a.furniture_text + a.outside;
            // A slot callout's crossed feature is a defect too ([`slot_callout`]).
            if jobs[k].1.slot {
                count += jobs[k].1.footprints.crossing_count([a.tip, a.elbow]) as u32;
            }
            for (j, &other) in selected.iter().enumerate().take(k) {
                count += conflict(a, &inv[j][other]);
            }
        }
        count
    };
    let (legacy_choice, legacy_capped) = assign(&baseline_original);
    let (mut baseline_choice, mut baseline_capped) = if semantic_pmi {
        (legacy_choice.clone(), legacy_capped.clone())
    } else {
        assign(&baseline)
    };
    if semantic_pmi
        || defects(&baseline, &baseline_choice) > defects(&baseline_original, &legacy_choice)
    {
        baseline = baseline_original;
        baseline_choice = legacy_choice;
        baseline_capped = legacy_capped;
    }
    // Furniture must not buy clearance by sending a leader through another feature.
    // Retain the established route as a candidate, and constrain each job by its prior floor.
    for (k, (_, job)) in jobs.iter().enumerate() {
        let b = &baseline[k][baseline_choice[k]];
        let ceiling = b.hard + b.crossings + b.outside;
        // A slot callout's crossed feature is a crossing like any other ([`slot_callout`]): a
        // route crossing a line instead of the baseline's feature stays a candidate.
        let features = if job.slot {
            job.footprints.crossing_count([b.tip, b.elbow]) as u32
        } else {
            0
        };
        inventories[k].retain(|c| {
            c.feature_crossings <= b.feature_crossings.max(features)
                && c.hard - c.furniture_hard + c.crossings - c.furniture_text
                    + c.outside
                    + if job.slot { c.feature_crossings } else { 0 }
                    <= ceiling + features
        });
        let right = b.end[0] > b.elbow[0];
        // Invariant: The incumbent route passed the same feasibility check before refinement.
        let mut prior = measure(job, b.tip, b.elbow, right, &fixed, page).expect("existing route");
        // Keep the text anchored when escaping fixed ink: nearby rim points, shorter shelves,
        // or the opposite outward side, all measured against the same feature evidence.
        if prior.furniture_hard > 0 {
            let site = nearest(&job.sites, b.elbow);
            if site.1 > 1e-9 {
                let radial = rim_tip(site, b.elbow);
                let u = [radial[0] - site.0[0], radial[1] - site.0[1]];
                for turn in ROTATIONS {
                    // New recess footprints can move a radius leader; keep those retries
                    // radial while preserving the canonical routes on unaffected drawings.
                    if recess_context
                        && items[jobs[k].0]
                            .measurements
                            .iter()
                            .any(|m| m.parameter == "slot_end_radius.radius")
                    {
                        continue;
                    }
                    let (sin, cos) = (turn * std::f64::consts::TAU / DIRECTIONS as f64).sin_cos();
                    let tip = [
                        site.0[0] + cos * u[0] - sin * u[1],
                        site.0[1] + sin * u[0] + cos * u[1],
                    ];
                    if let Some(mut c) = measure_at(job, tip, b.elbow, b.end, right, &fixed, page)
                        && c.feature_crossings <= b.feature_crossings
                    {
                        c.aware = !semantic_pmi;
                        inventories[k].push(c);
                    }
                }
            }
            for offset in SHOULDER_STEPS.map(|f| f * SHOULDER) {
                let elbow = [
                    b.elbow[0] + if right { offset } else { -offset },
                    b.elbow[1],
                ];
                let tip = rim_tip(nearest(&job.sites, elbow), elbow);
                let across = [
                    b.elbow[0] + if right { -offset } else { offset },
                    b.elbow[1],
                ];
                let other_tip = rim_tip(nearest(&job.sites, across), across);
                if let Some(mut c) = measure(job, other_tip, across, !right, &fixed, page)
                    && c.feature_crossings <= b.feature_crossings
                {
                    c.aware = !semantic_pmi;
                    inventories[k].push(c);
                }
                if let Some(mut c) = measure_at(job, tip, elbow, b.end, right, &fixed, page) {
                    if c.feature_crossings > b.feature_crossings {
                        continue;
                    }

                    c.aware = !semantic_pmi;
                    inventories[k].push(c);
                }
            }
        }
        prior.aware = !semantic_pmi;
        inventories[k].push(prior);
    }
    if semantic_pmi {
        // A slot callout is new to these drawings, so it has no legacy route to keep: its
        // leader still counts the features it crosses ([`slot_callout`]).
        for (inv, (_, job)) in inventories.iter_mut().zip(&jobs) {
            for c in inv.iter_mut().filter(|_| !job.slot) {
                c.aware = false;
            }
        }
    }
    let (mut choice, mut capped) = assign(&inventories);
    if defects(&inventories, &choice) > defects(&baseline, &baseline_choice) {
        // Restore the incumbent when the bounded joint solve spends its ordinary ink floor.
        choice = inventories.iter().map(|inv| inv.len() - 1).collect();
        capped = baseline_capped;
    }
    let floor = defects(&baseline, &baseline_choice);
    for _ in 0..DESCENT {
        let before = choice.clone();
        for k in 0..inventories.len() {
            if inventories[k][choice[k]].furniture_hard == 0 {
                continue;
            }
            let ordinary = |c: &Candidate| {
                c.hard - c.furniture_hard + c.crossings - c.furniture_text + c.outside
            };
            let local = |c: &Candidate| {
                ordinary(c)
                    + choice
                        .iter()
                        .enumerate()
                        .filter(|(j, _)| *j != k)
                        .map(|(j, &n)| conflict(c, &inventories[j][n]))
                        .sum::<u32>()
            };
            let ceiling = local(&inventories[k][choice[k]])
                .saturating_add(floor)
                .saturating_sub(defects(&inventories, &choice));
            choice[k] = inventories[k]
                .iter()
                .enumerate()
                .filter(|(_, c)| local(c) <= ceiling)
                .min_by_key(|(n, c)| (c.score() + i64::from(local(c) - ordinary(c)) * CROSSING, *n))
                .map(|(n, _)| n)
                // A remeasured incumbent can exceed the legacy floor after a new
                // corridor is installed. Keep it until a safe improvement exists.
                .unwrap_or(choice[k]);
        }
        if choice == before {
            break;
        }
    }
    let mut problems = Vec::new();
    for ((i, job), (inv, c)) in jobs.iter().zip(inventories.iter().zip(choice)) {
        let Some(chosen) = inv.get(c) else { continue };
        placed[*i] = Some(chosen.placed());
        problems.extend(route_reports(
            &items[*i],
            job.view,
            job.footprints.solids(),
            [chosen.tip, chosen.elbow],
        ));
    }
    for group in capped {
        let subjects: Vec<String> = group.iter().map(|&k| items[jobs[k].0].id.clone()).collect();
        problems.push(Problem {
            id: format!("p:annotation_leader_search_capped:{}", subjects.join("|")),
            code: "annotation_leader_search_capped".into(),
            severity: "info".into(),
            message: format!(
                "The leaders of {} callouts were placed together by a search that stopped at its bound of {NODE_BUDGET} steps: their places are the best found, not proved the best.",
                subjects.len()
            ),
            subjects,
            ..Default::default()
        });
    }
    problems
}

/// Geometry findings are rebuilt when a refinement replaces their route.
pub(super) fn route_reports(
    item: &Want,
    view_name: &str,
    solids: &[[f64; 4]],
    shaft: [P2; 2],
) -> Vec<Problem> {
    route_reports_named(&item.id, &super::wording_of(item), view_name, solids, shaft)
}

fn route_reports_named(
    id: &str,
    named: &str,
    view_name: &str,
    solids: &[[f64; 4]],
    shaft: [P2; 2],
) -> Vec<Problem> {
    let mut problems = Vec::new();
    let view = format!("v:{}", view_name);
    let cut = crate::material::reentry(solids, shaft);
    if cut > 0.0 {
        let subjects = vec![id.to_owned(), view.clone()];
        problems.push(Problem {
                id: format!("p:annotation_line_over_view:{}", subjects.join("|")),
                code: "annotation_line_over_view".into(),
                severity: "warning".into(),
                message: format!(
                    "The leader of {} runs back across its own view, {}, cutting {cut:.1} mm of it: every place it had was blocked.",
                    named,
                    crate::wording::view(&view)
                ),
                subjects,
                ..Default::default()
            });
    }
    let length = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
    if too_long(length) {
        let subjects = vec![id.to_owned()];
        problems.push(Problem {
                id: format!("p:annotation_leader_long:{}", subjects.join("|")),
                code: "annotation_leader_long".into(),
                severity: "warning".into(),
                message: format!(
                    "The leader of {} is {length:.1} mm long, over the {MAX_LEADER} mm a leader may run: every shorter place it had was blocked.",
                    named
                ),
                subjects,
                ..Default::default()
            });
    }
    problems
}

/// Retry free callouts hit by furniture placed after the initial joint solve. Only an
/// actual text contact triggers the full side/row/tier search; unaffected routes stay fixed.
#[allow(clippy::too_many_arguments)]
pub(crate) fn settle_after_furniture(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    items: &mut [PlannedDimension],
    obstacles: &[PlannedDimension],
    cache: &super::footprints::Cache,
    final_inventory: bool,
) -> BTreeMap<String, Vec<Problem>> {
    // Authored PMI keeps the established furniture repair policy. The wider
    // nominal search belongs to automatically generated geometry statements.
    let compact = (!final_inventory
        && crate::nominal::geometry_eligible(analysis)
        && default_nominal_intent(spec))
        || crate::nominal::comparison()
        || crate::nominal::authored_pmi(analysis);
    if !compact {
        super::nearest_shared_witnesses(analysis, spec, views, items);
    }
    let bounded_notches = !crate::notches::requirements(analysis).is_empty();
    let references = final_inventory
        .then(|| settled_nominal_reference(analysis, spec, sheet, items))
        .flatten();
    let full_reference = references.as_ref().map(|r| &r.full);
    let legacy_reference = references.as_ref().and_then(|r| r.legacy.as_ref());
    let all_extensions: Vec<_> = items
        .iter()
        .enumerate()
        .filter(|(_, d)| witnessed(d))
        .flat_map(|(i, d)| {
            d.placed
                .iter()
                .flat_map(move |p| p.lines.iter().take(2).map(move |s| (i, *s)))
        })
        .collect();
    let mut updated = BTreeMap::new();
    let mut wants = None;
    let mut profile_proofs = None;
    let page = Page::new(analysis, sheet, views);
    for i in 0..items.len() {
        let d = &items[i];
        let pin = spec.dimensions.get(&d.id).and_then(|e| e.place.as_ref());
        let held = pin.is_some();
        let radius = final_inventory
            && d.measurements.iter().any(|m| {
                m.parameter == "slot_end_radius.radius"
                    && crate::nominal::format(m.value)
                        .split_once('.')
                        .is_some_and(|(_, tail)| tail.len() > 1)
            });
        if d.kind != "callout"
            || (!compact && d.region == Some(super::CandidateRegion::Interior))
            || pin.is_some_and(|p| {
                !crate::placement_intent::automatic_pin(p)
                    || !crate::nominal::geometry_eligible(analysis)
            })
        {
            continue;
        }
        let Some(old) = &d.placed else { continue };
        // Preserve established corridors against the pre-recess comparison,
        // rather than treating a displaced full-inventory route as its own budget.
        let matching = |r: &crate::model::Plan| {
            r.dimensions
                .iter()
                .find(|b| {
                    b.id == d.id
                        && b.view == d.view
                        && b.region == d.region
                        && b.measurements.iter().all(|m| d.measurements.contains(m))
                })
                .and_then(|b| b.placed.as_ref())
                .map(|p| length(&p.lines))
        };
        let reference = match (legacy_reference, full_reference) {
            (Some(legacy), Some(full))
                if matching(legacy)
                    .is_some_and(|l| matching(full).is_none_or(|f| l <= f + 1e-6)) =>
            {
                Some(legacy)
            }
            (_, full) => full,
        };
        // Longer faithful nominal labels can contact already settled dimensions as well
        // as furniture. Reconsider those routes against the complete live inventory.
        let conflicts = if compact {
            obstacles
                .iter()
                .chain(
                    items
                        .iter()
                        .filter(|d| bounded_notches && d.kind == "linear"),
                )
                .filter_map(|d| d.placed.as_ref())
                .any(|p| super::text_contact(old, p))
        } else {
            obstacles
                .iter()
                .chain(
                    items
                        .iter()
                        .enumerate()
                        .filter(|(j, _)| *j != i)
                        .map(|(_, d)| d),
                )
                .filter_map(|d| d.placed.as_ref())
                .any(|p| {
                    super::text_contact(old, p)
                        || old
                            .lines
                            .iter()
                            .any(|a| p.lines.iter().chain(&p.arrows).any(|b| lines_meet(*a, *b)))
                })
        };
        let padded_contact =
            final_inventory && extension_contacts(&all_extensions, &old.lines, old.label_box).1 > 0;
        if !conflicts && reference.is_none() && !radius && !padded_contact {
            continue;
        }
        let Some(w) = wants
            .get_or_insert_with(|| {
                let mut wants = super::wants(analysis).0;
                let frames = views
                    .iter()
                    .filter_map(|v| {
                        let name = v.id.strip_prefix("v:")?;
                        (super::NAMES.contains(&name) || crate::detail::NAMES.contains(&name))
                            .then(|| (name, Frame::new(analysis, v)))
                    })
                    .collect();
                // Late repairs need the same proven profile sites as the initial
                // router, including section and detail transformations.
                if !compact {
                    super::detail_routes(analysis, &mut wants, views);
                    super::route(&mut wants, &frames, analysis);
                }
                wants
            })
            .iter()
            .find(|w| w.id == d.id)
        else {
            continue;
        };
        let mut printed = w.clone();
        printed.terms.clone_from(&d.terms);
        printed.measurements.clone_from(&d.measurements);
        if !compact
            && crate::nominal::geometry_eligible(analysis)
            && let Shape::Callout { sites, arcs } = &mut printed.shape
        {
            // Consolidation may leave one printed callout claiming several
            // equivalent occurrences. Every claimed occurrence visible in this
            // view remains a valid attachment after its raw alias is conveyed.
            for peer in wants.as_ref().into_iter().flatten().filter(|peer| {
                peer.id != d.id
                    && peer.views.contains(&d.view.trim_start_matches("v:"))
                    && peer.measurements.iter().any(|m| d.measurements.contains(m))
            }) {
                if let Shape::Callout {
                    sites: more,
                    arcs: span,
                } = &peer.shape
                {
                    sites.extend(more.iter().copied());
                    arcs.extend(span.iter().cloned());
                }
            }
        }
        let w = &printed;
        let (Shape::Callout { sites, arcs }, Some(v)) =
            (&w.shape, views.iter().find(|v| v.id == d.view))
        else {
            continue;
        };
        let frame = Frame::new(analysis, v);
        let view = v.id.trim_start_matches("v:");
        let ink = label_ink(&d.terms, font());
        let footprint = cache.get(analysis, v);
        let model = if !compact || grazing(&frame, w).is_some() {
            cache.model(analysis, v)
        } else {
            None
        };
        let inspection = if compact {
            None
        } else {
            model.as_ref().and_then(|ink| ink.0.inspection(analysis, v))
        };
        let profile_proof = if !compact && boss_profile(w) {
            profile_proofs
                .get_or_insert_with(|| super::interior::proofs(analysis, views))
                .get(&v.id)
                .map(|proof| (v, proof.as_ref()))
        } else {
            None
        };
        let job = Job {
            side: spec
                .dimensions
                .get(&d.id)
                .and_then(|e| e.place.as_ref())
                .and_then(crate::placement_intent::side_hint),
            view,
            footprints: &footprint,
            outline: frame.outline(),
            area: page.area(view),
            sites: page_sites(&frame, sites, arcs),
            radial: !crate::nominal::comparison()
                && w.measurements.iter().any(|m| {
                    m.parameter == "slot_end_radius.radius"
                        && crate::nominal::format(m.value)
                            .split_once('.')
                            .is_some_and(|(_, tail)| tail.len() > 1)
                }),
            directed: false,
            // Enable the existing bounded corridor retry for a newly contacted route.
            corridors: !compact,
            slot: slot_callout(w),
            short_profile: boss_profile(w),
            inward: false,
            edge_attachment: true,
            interior_proof: None,
            profile_proof,
            silhouette: grazing(&frame, w),
            model: model.as_ref().map(|m| &m.as_ref().0),
            steer: std::cell::Cell::new(None),
            ink_min: ink.min,
            ink_max: ink.max,
            stroke_pad: stroke_clearance(&d.terms),
            reach: own_reach(&w.measurements, frame.scale()),
            beyond: [CLEAR; 4],
        };
        let others: Vec<_> = items
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .map(|(_, d)| d)
            .chain(obstacles)
            .collect();
        let fixed = Fixed::new(
            others
                .iter()
                .enumerate()
                .filter_map(|(j, d)| Some((j, d.placed.as_ref()?))),
        );
        let ordinary_inventory = || {
            inventory(
                &job,
                &fixed,
                &page,
                false,
                job.stroke_pad > 0.0
                    && !crate::notches::requirements(analysis).is_empty()
                    && w.measurements
                        .iter()
                        .any(|m| m.parameter == "polygon_across_flats.length"),
            )
            .0
        };
        let mut ordinary =
            (!held && (!final_inventory || job.silhouette.is_some())).then(ordinary_inventory);
        let [tip, elbow] = old.lines[0];
        let end = old.lines[1][1];
        let Some(before) = measure_at(&job, tip, elbow, end, end[0] >= elbow[0], &fixed, &page)
        else {
            continue;
        };
        let radial = |c: &Candidate| {
            !radius
                || job.sites.iter().any(|&(centre, r, _)| {
                    let u = [c.tip[0] - centre[0], c.tip[1] - centre[1]];
                    let v = [c.elbow[0] - c.tip[0], c.elbow[1] - c.tip[1]];
                    let norm = u[0].hypot(u[1]) * v[0].hypot(v[1]);
                    r > 0.0
                        && (u[0].hypot(u[1]) - r).abs()
                            <= frame.scale() * crate::model::projected_ink::GRID
                        && (u[0] * v[1] - u[1] * v[0]).abs() <= 1e-9 * norm
                        && u[0] * v[0] + u[1] * v[1] > 0.0
                })
        };
        let mut candidates = Vec::new();
        let new_recess = legacy_reference.is_some_and(|r| matching(r).is_none())
            && d.measurements
                .iter()
                .all(|m| m.feature.starts_with("f:section_recess:"));
        let budget = reference.as_ref().map(|r| {
            r.dimensions
                .iter()
                .find(|b| b.id == d.id && b.region == d.region)
                .and_then(|b| b.placed.as_ref())
                .map_or(length(&old.lines), |p| {
                    // A new recess has no established paper corridor. Its compact
                    // comparison may use a different scale; map that model reach
                    // before comparing it with the current paper route.
                    let reach = if new_recess {
                        r.views
                            .iter()
                            .find(|q| q.id == d.view)
                            .map_or(length(&p.lines), |q| {
                                let shaft = length(&p.lines[..1]) * v.scale / q.scale;
                                shaft + length(&p.lines[1..])
                            })
                    } else {
                        length(&p.lines)
                    };
                    reach.min(length(&old.lines))
                })
                + 2.0
        });
        // A repair cannot lengthen an already shorter live corridor simply
        // because the compact recipe took a longer path on another sheet.
        if !held && reference.is_some() {
            let delta = [elbow[0] - tip[0], elbow[1] - tip[1]];
            let norm = delta[0].hypot(delta[1]);
            for reach in [MIN_LEADER, 7.0, 8.0, 9.0, 11.0, 14.0, norm] {
                let elbow = [0, 1].map(|k| tip[k] + delta[k] * reach / norm);
                for right in [false, true] {
                    for shoulder in [0.75, 3.0] {
                        let end = [
                            elbow[0] + if right { shoulder } else { -shoulder },
                            elbow[1],
                        ];
                        let attached = moved_rim_tip(
                            &job.sites,
                            tip,
                            elbow,
                            frame.scale() * crate::model::projected_ink::GRID,
                        );
                        if let Some(c) =
                            measure_at(&job, attached, elbow, end, right, &fixed, &page)
                        {
                            candidates.push(c);
                        }
                    }
                }
            }
        }
        let established = reference.as_ref().and_then(|reference| {
            let old = reference.dimensions.iter().find(|old| {
                old.id == d.id
                    && old.view == d.view
                    && old.region == d.region
                    && old.measurements.iter().all(|m| d.measurements.contains(m))
            })?;
            let old_view = reference.views.iter().find(|old_view| {
                old_view.id == d.view
                    && old_view.direction == v.direction
                    && old_view.up == v.up
                    && old_view.cut == v.cut
                    && old_view.detail == v.detail
            })?;
            let p = old.placed.as_ref()?;
            let [old_tip, old_elbow] = p.lines[0];
            let factor = v.scale / old_view.scale;
            let map = |q: P2| [0, 1].map(|k| v.at[k] + factor * (q[k] - old_view.at[k]));
            let tip = map(old_tip);
            let mapped_elbow = map(old_elbow);
            let delta = [mapped_elbow[0] - tip[0], mapped_elbow[1] - tip[1]];
            let norm = delta[0].hypot(delta[1]);
            if norm < 1e-9 {
                return None;
            }
            let elbow = [0, 1].map(|k| tip[k] + delta[k] * norm.max(MIN_LEADER) / norm);
            let right = p.lines[1][1][0] >= old_elbow[0];
            let shoulder = (p.lines[1][1][0] - old_elbow[0]).abs();
            let end = [
                elbow[0] + if right { shoulder } else { -shoulder },
                elbow[1],
            ];
            let budget = length(&p.lines) + 2.0;
            let baseline = measure_at(&job, tip, elbow, end, right, &fixed, &page)?;
            // Model attachments scale with the projection, but the shoulder and
            // minimum readable leader length remain paper measurements.
            if !held && norm > 1e-9 {
                for reach in [MIN_LEADER, 7.0, 8.0, 9.0, 11.0, 14.0, norm] {
                    let elbow = [0, 1].map(|k| tip[k] + delta[k] * reach / norm);
                    for right in [right, !right] {
                        for shoulder in [0.75, 3.0, shoulder] {
                            let end = [
                                elbow[0] + if right { shoulder } else { -shoulder },
                                elbow[1],
                            ];
                            let attached = moved_rim_tip(
                                &job.sites,
                                tip,
                                elbow,
                                frame.scale() * crate::model::projected_ink::GRID,
                            );
                            if let Some(c) =
                                measure_at(&job, attached, elbow, end, right, &fixed, &page)
                            {
                                candidates.push(c);
                            }
                        }
                    }
                }
            }
            Some((baseline, budget))
        });
        if let Some((c, _)) = &established {
            candidates.push(c.clone());
        }
        let extensions: Vec<_> = others
            .iter()
            .enumerate()
            .filter(|(_, d)| witnessed(d))
            .flat_map(|(i, d)| {
                d.placed
                    .as_ref()
                    .into_iter()
                    .flat_map(move |p| p.lines.iter().take(2).map(move |line| (i, *line)))
            })
            .collect();
        let prior_extensions = extension_contacts(&extensions, &old.lines, old.label_box);
        let extension_ink: Vec<_> = extensions.iter().map(|(_, e)| *e).collect();
        let extension_run =
            |c: &Candidate| c.segments().iter().any(|s| runs_beside(*s, &extension_ink));
        // Score the final collision predicate as well as the router's indexed tiers.
        // Near-parallel runs and contacts with the other's arrow shaft must disappear
        // on the live ink, including the reverse direction of a callout pair.
        let live_contacts = |p: &Placed| -> std::collections::BTreeSet<usize> {
            others
                .iter()
                .enumerate()
                .filter_map(|(i, d)| {
                    let q = d.placed.as_ref()?;
                    let forward = p
                        .lines
                        .iter()
                        .any(|a| q.lines.iter().chain(&q.arrows).any(|b| lines_meet(*a, *b)));
                    let reverse = d.kind == "callout"
                        && q.lines
                            .iter()
                            .any(|a| p.lines.iter().chain(&p.arrows).any(|b| lines_meet(*a, *b)));
                    (forward || reverse || super::text_contact(p, q)).then_some(i)
                })
                .collect()
        };
        let prior_live = live_contacts(old);
        let count_heads = |c: &Candidate, extensions: &[(usize, [P2; 2])]| {
            extensions
                .iter()
                .filter(|(_, e)| {
                    head(arrow(c.tip, c.elbow))
                        .iter()
                        .any(|h| touches_box(*h, grow(segment_bounds(*e), 0.2)))
                })
                .count()
        };
        let head_contacts = |c: &Candidate| count_heads(c, &extensions);
        let outline_pad = LABEL_PAD
            + (crate::model::projected_ink::TOLERANCE + crate::model::projected_ink::GRID)
                * v.scale;
        let outline_contacts = |c: &Candidate| {
            job.model.map_or((0, 0), |ink| {
                let ink = inspection.map_or(ink, |r| &r.visible);
                (
                    ink.touching(grow(c.label_box, outline_pad)),
                    ink.touching(grow(segment_bounds([c.elbow, c.elbow]), outline_pad)),
                )
            })
        };
        let hidden_contacts =
            |c: &Candidate| inspection.map(|ink| ink.hidden.route_crossings(c.segments()));
        let drawn_run = |c: &Candidate| {
            let drawn = inspection.map(|ink| &ink.drawn).or_else(|| {
                (final_inventory && crate::nominal::geometry_eligible(analysis))
                    .then_some(job.model)
                    .flatten()
            });
            drawn.is_some_and(|ink| c.segments().iter().any(|s| ink.runs_beside(*s)))
                || extension_run(c)
        };
        let baseline = established.as_ref().map_or(&before, |(b, _)| b);
        // Spend the established drawing's crossing budget, rather than making
        // an incidental reduction on a long detour permanent. This permits a
        // shorter corridor while staying no worse than the established ink.
        let hidden_limit = hidden_contacts(baseline).or_else(|| hidden_contacts(&before));
        let hidden_clear = |c: &Candidate| {
            hidden_contacts(c)
                .zip(hidden_limit)
                .is_none_or(|(now, limit)| now <= limit)
        };
        let historical_extensions: Vec<_> = reference
            .iter()
            .flat_map(|r| {
                r.dimensions
                    .iter()
                    .enumerate()
                    .filter(|(_, d)| witnessed(d))
                    .filter_map(|(i, d)| {
                        let old = r.views.iter().find(|v| v.id == d.view)?;
                        let now = views.iter().find(|v| {
                            v.id == d.view && v.direction == old.direction && v.up == old.up
                        })?;
                        let p = d.placed.as_ref()?;
                        let factor = now.scale / old.scale;
                        Some(p.lines.iter().take(2).map(move |line| {
                            (
                                i,
                                line.map(|q| {
                                    [0, 1].map(|k| now.at[k] + factor * (q[k] - old.at[k]))
                                }),
                            )
                        }))
                    })
                    .flatten()
            })
            .collect();
        let head_limit = if historical_extensions.is_empty() {
            head_contacts(&before)
        } else {
            count_heads(baseline, &historical_extensions)
        };
        let current_outline = outline_contacts(&before);
        let baseline_outline = outline_contacts(baseline);
        let outline_limit = (
            current_outline.0.min(baseline_outline.0),
            current_outline.1.min(baseline_outline.1),
        );
        let heads_clear = |c: &Candidate| head_contacts(c) <= head_limit;
        let outline_clear = |c: &Candidate| {
            let contacts = outline_contacts(c);
            let pad = grow(c.label_box, LABEL_PAD);
            let page_pad = page.drawable();
            contacts.0 <= outline_limit.0
                && contacts.1 <= outline_limit.1
                && (reference.is_none()
                    || (pad[0] >= page_pad[0]
                        && pad[1] >= page_pad[1]
                        && pad[2] <= page_pad[2]
                        && pad[3] <= page_pad[3]))
        };
        // A pre-existing ink run can be retained only with its established
        // text too. Retain that compact label on its exact shaft before
        // searching for another corridor; never move a held leader.
        if final_inventory
            && inspection.is_none()
            && !compact
            && drawn_run(&before)
            && let Some((baseline, _)) = &established
            && before
                .segments()
                .iter()
                .flatten()
                .zip(baseline.segments().iter().flatten())
                .all(|(a, b)| (a[0] - b[0]).hypot(a[1] - b[1]) <= 1e-6)
            && let Some(previous) = reference.and_then(|r| {
                r.dimensions
                    .iter()
                    .find(|b| b.id == d.id && b.view == d.view && b.region == d.region)
            })
            && previous.terms != d.terms
        {
            let ink = label_ink(&previous.terms, font());
            let mut compact_job = job.clone();
            compact_job.ink_min = ink.min;
            compact_job.ink_max = ink.max;
            if let Some(c) = measure_at(
                &compact_job,
                tip,
                elbow,
                end,
                end[0] >= elbow[0],
                &fixed,
                &page,
            ) && (0..2).all(|k| {
                c.label_box[k] >= old.label_box[k] - 1e-6
                    && c.label_box[k + 2] <= old.label_box[k + 2] + 1e-6
            }) && c.text <= before.text
                && c.hard <= before.hard
                && c.crossings <= before.crossings
            {
                let terms = previous.terms.clone();
                let id = d.id.clone();
                let mut restored = old.clone();
                restored.label_at = c.label_at;
                restored.label_box = c.label_box;
                updated.insert(id, route_reports(w, view, footprint.solids(), [tip, elbow]));
                items[i].label = terms.join(" ");
                items[i].terms = terms;
                items[i].placed = Some(restored);
                items[i].basis +=
                    "; established one-decimal text retained on its unchanged model-ink run";
                continue;
            }
        }
        if drawn_run(&before)
            && established.as_ref().is_some_and(|(b, _)| {
                before
                    .segments()
                    .iter()
                    .flatten()
                    .zip(b.segments().iter().flatten())
                    .all(|(a, b)| (a[0] - b[0]).hypot(a[1] - b[1]) <= 1e-6)
            })
            && reference.is_some_and(|r| {
                r.dimensions
                    .iter()
                    .any(|b| b.id == d.id && b.terms == d.terms)
            })
            && prior_live.is_empty()
            && prior_extensions.1 == 0
            && heads_clear(&before)
        {
            continue;
        }
        let historical_ink_clear = |c: &Candidate| {
            inspection.is_some()
                || established.as_ref().is_none_or(|(b, _)| {
                    !job.model
                        .is_some_and(|ink| ink.new_physical_crossing(&c.segments(), &b.segments()))
                })
        };
        // Good routes have no growth repair to perform. In particular, do not
        // spend a local search budget moving an already clear neighbouring callout.
        if (reference.is_some() || radius)
            && before.clear()
            && radial(&before)
            && before.feature_crossings == 0
            && before.beside == 0
            && before.grazing == 0
            && prior_live.is_empty()
            && prior_extensions == (0, 0)
            && heads_clear(&before)
            && outline_clear(&before)
            && hidden_clear(&before)
            && historical_ink_clear(&before)
            && !drawn_run(&before)
            && budget.is_none_or(|budget| length(&before.segments()) <= budget + 1e-6)
            && established
                .as_ref()
                .is_none_or(|(b, _)| model_crossings(&job, &before) <= model_crossings(&job, b))
        {
            continue;
        }
        if !held {
            let mut ordinary = ordinary.take().unwrap_or_else(ordinary_inventory);
            ordinary.extend(candidates);
            candidates = ordinary;
        }
        let acceptable = |c: &Candidate| {
            // A moved radius statement must point towards its actual arc centre.
            if !radial(c) {
                return false;
            }
            // A shorter shelf still reads in the direction its shaft leaves the rim.
            if (c.elbow[0] - c.tip[0]) * (c.end[0] - c.elbow[0]) < -1e-9 {
                return false;
            }
            if compact {
                return c.text < before.text
                    && c.hard <= before.hard
                    && c.crossings <= before.crossings
                    && c.feature_crossings <= before.feature_crossings
                    && c.furniture <= before.furniture
                    && page.fits(&c.placed(), view);
            }
            if budget.is_some_and(|budget| length(&c.segments()) > budget + 1e-6) {
                return false;
            }
            // The measured counts below are required terms of the conjunction that follows;
            // refuse on them before paying for the live-contact and extension scans.
            if !(c.text <= before.text
                && c.hard <= before.hard
                && c.crossings <= before.crossings
                && c.furniture <= before.furniture
                && c.grazing <= before.grazing
                && c.beside <= before.beside)
            {
                return false;
            }
            let live = live_contacts(&c.placed());
            let contacts = extension_contacts(&extensions, &c.segments(), c.label_box);
            (c.text < before.text
                || !radial(&before)
                || live.len() < prior_live.len()
                || contacts.1 < prior_extensions.1
                || (reference.is_some()
                    && (c.score() < before.score()
                        || c.feature_crossings < before.feature_crossings
                        || model_crossings(&job, c) < model_crossings(&job, &before)
                        || contacts < prior_extensions
                        || !heads_clear(&before)
                        || !outline_clear(&before)
                        || !hidden_clear(&before)
                        || (drawn_run(&before) && !drawn_run(c)))))
                && live.is_subset(&prior_live)
                && c.text <= before.text
                && c.hard <= before.hard
                && c.crossings <= before.crossings
                && c.feature_crossings
                    <= established
                        .as_ref()
                        .map_or(before.feature_crossings, |(b, _)| {
                            before.feature_crossings.max(b.feature_crossings)
                        })
                && c.furniture <= before.furniture
                // The exterior shoulder must not trade an annotation contact for
                // a new pass through its own view, even if another hard cost fell.
                && (!super::placement::crosses([c.elbow, c.end], job.outline)
                    || super::placement::crosses([before.elbow, before.end], job.outline))
                && c.grazing <= before.grazing
                && c.beside <= before.beside
                && c.clearance
                    <= established
                        .as_ref()
                        .map_or(before.clearance, |(b, _)| before.clearance.max(b.clearance))
                && model_crossings(&job, c)
                    <= established
                        .as_ref()
                        .map_or(model_crossings(&job, &before), |(b, _)| {
                            model_crossings(&job, &before).max(model_crossings(&job, b))
                        })
                && contacts.0 <= prior_extensions.0
                && contacts.1 <= prior_extensions.1
                && !drawn_run(c)
                && hidden_clear(c)
                && historical_ink_clear(c)
                && (reference.is_none() || {
                    let outline = job.model;
                    (!outline.is_some_and(|ink| {
                        ink.new_physical_crossing(&c.segments(), &before.segments())
                    }) || model_crossings(&job, c) < model_crossings(&job, &before)
                        // A genuine hard-contact repair may use another edge at
                        // the established intersection budget; a shorter route
                        // alone does not license that exchange.
                        || ((c.hard < before.hard || hidden_contacts(c).zip(hidden_contacts(&before)).is_some_and(|(a,b)|a<b)) && established.as_ref().is_some_and(|(b, _)| model_crossings(&job, c) <= model_crossings(&job, b)))
                        || established.as_ref().is_some_and(|(baseline, _)| {
                            !outline.is_some_and(|ink| {
                                ink.new_physical_crossing(&c.segments(), &baseline.segments())
                            })
                        }))
                        && outline_clear(c)
                        && heads_clear(c)
                })
                && (page.fits(&c.placed(), view)
                    || (reference.is_some() && {
                        let mut reserved = views.to_vec();
                        let mut ink = items.to_vec();
                        ink[i].placed = Some(c.placed());
                        crate::sheet_fit::reserve_nominal_bounds(
                            analysis,
                            sheet,
                            &mut reserved,
                            &ink,
                            &[],
                        );
                        Page::new(analysis, sheet, &reserved).fits(&c.placed(), view)
                    }))
        };
        let rank = |c: &Candidate| {
            let contacts = extension_contacts(&extensions, &c.segments(), c.label_box);
            // Hidden and physical crossing budgets are enforced above. Within
            // those budgets prefer a clear, short readable corridor; one fewer
            // hidden edge must not buy an unnecessarily long detour.
            (
                c.hard,
                c.text,
                c.feature_crossings,
                head_contacts(c),
                outline_contacts(c),
                contacts.1,
                c.beside,
                contacts.0,
                (length(&c.segments()) * 1e6).round() as i64,
                established.as_ref().map_or(0, |(b, _)| {
                    job.model.map_or(0, |ink| {
                        ink.new_physical_count(&c.segments(), &b.segments())
                    })
                }),
                hidden_contacts(c).unwrap_or(0),
                c.score(),
            )
        };
        let partial = candidates.into_iter().filter(&acceptable).min_by(|a, b| {
            if compact {
                a.score().cmp(&b.score())
            } else {
                rank(a).cmp(&rank(b))
            }
        });
        let next = if held {
            None
        } else {
            partial
                .clone()
                .filter(|c| reference.is_none() || c.clear())
                .or_else(|| {
                    // The final label may have grown into a peer's corridor. Keep its proven
                    // attachment and try bounded half-mm elbow/shelf translations, nearest first.
                    // Every material, furniture, text and arrowhead gate still applies.
                    if compact {
                        return None;
                    }
                    // `measure_at`, except that a route `acceptable` must refuse on its geometry
                    // alone (its direction, shelf and length budget), or whose page, view and
                    // material blockers or grazing already exceed the old route's, is not
                    // measured further: the annotation tier only adds to `hard`.
                    let ring_measure = |tip: P2, elbow: P2, end: P2, right: bool| {
                        // A measured candidate keeps these tip, elbow and end points.
                        if (elbow[0] - tip[0]) * (end[0] - elbow[0]) < -1e-9
                            || budget.is_some_and(|budget| {
                                length(&[[tip, elbow], [elbow, end]]) > budget + 1e-6
                            })
                        {
                            return None;
                        }
                        let mut c = measure_at_bounds(&job, tip, elbow, end, right, &page)?;
                        if !radial(&c) || c.hard > before.hard || c.grazing > before.grazing {
                            return None;
                        }
                        measure_fixed(&mut c, &job, &fixed);
                        c.measure_features(&job);
                        Some(c)
                    };
                    let mut fallback = partial.clone();
                    let mut centres = vec![(tip, elbow)];
                    if let Some((b, _)) = &established {
                        centres.push((b.tip, b.elbow));
                    }
                    for (tip, elbow) in centres {
                        for ring in 1_i32..=if reference.is_some() { 32 } else { 14 } {
                            for dx in -ring..=ring {
                                for dy in -ring..=ring {
                                    if dx.abs().max(dy.abs()) != ring {
                                        continue;
                                    }
                                    let elbow = [
                                        elbow[0] + f64::from(dx) * 0.5,
                                        elbow[1] + f64::from(dy) * 0.5,
                                    ];
                                    if reference.is_none() {
                                        let right = elbow[0] >= tip[0];
                                        let end = [
                                            elbow[0] + if right { SHOULDER } else { -SHOULDER },
                                            elbow[1],
                                        ];
                                        if let Some(c) = ring_measure(tip, elbow, end, right)
                                            && acceptable(&c)
                                        {
                                            return Some(c);
                                        }
                                        continue;
                                    }
                                    for right in [elbow[0] >= tip[0], elbow[0] < tip[0]] {
                                        for shoulder in [0.75, 3.0] {
                                            let end = [
                                                elbow[0] + if right { shoulder } else { -shoulder },
                                                elbow[1],
                                            ];
                                            let attached = moved_rim_tip(
                                                &job.sites,
                                                tip,
                                                elbow,
                                                frame.scale() * crate::model::projected_ink::GRID,
                                            );
                                            if let Some(c) =
                                                ring_measure(attached, elbow, end, right)
                                                && acceptable(&c)
                                            {
                                                if c.clear() {
                                                    return Some(c);
                                                }
                                                // Dense views can retain an old crossing while
                                                // repairing a padded text/head/outline contact.
                                                // Keep that improvement if no clear route exists.
                                                if fallback
                                                    .as_ref()
                                                    .is_none_or(|b| c.score() < b.score())
                                                {
                                                    fallback = Some(c);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    fallback
                })
                .or(partial)
        };
        let Some(next) = next else {
            continue;
        };
        let mut printed = w.clone();
        printed.terms.clone_from(&d.terms);
        updated.insert(
            d.id.clone(),
            route_reports(&printed, view, footprint.solids(), [next.tip, next.elbow]),
        );
        items[i].placed = Some(next.placed());
    }
    updated
}

fn radius_contacts<'a>(
    p: &Placed,
    others: impl IntoIterator<Item = &'a PlannedDimension>,
) -> std::collections::BTreeSet<(&'a str, u8)> {
    let mut pairs = std::collections::BTreeSet::new();
    for q in others {
        let Some(qp) = &q.placed else { continue };
        if super::placement::ink_through(p, qp.label_box) {
            pairs.insert((q.id.as_str(), 1u8));
        }
        if super::placement::ink_through(qp, p.label_box) {
            pairs.insert((q.id.as_str(), 2u8));
        }
        if boxes_overlap(p.label_box, qp.label_box) {
            pairs.insert((q.id.as_str(), 3u8));
        }
        if q.kind == "linear" {
            for witness in qp.lines.iter().take(2) {
                if touches_box(*witness, grow(p.label_box, LABEL_PAD))
                    || p.lines
                        .iter()
                        .any(|line| touches_box(*line, grow(segment_bounds(*witness), LABEL_PAD)))
                {
                    pairs.insert((q.id.as_str(), 5u8));
                }
                if p.arrows
                    .iter()
                    .flat_map(|a| head(*a))
                    .any(|line| touches_box(line, grow(segment_bounds(*witness), 0.2)))
                {
                    pairs.insert((q.id.as_str(), 6u8));
                }
            }
        }
        if p.lines.iter().any(|a| {
            qp.lines
                .iter()
                .chain(&qp.arrows)
                .any(|b| lines_meet(*a, *b))
        }) {
            pairs.insert((q.id.as_str(), 0u8));
        }
        if qp
            .lines
            .iter()
            .any(|a| p.lines.iter().chain(&p.arrows).any(|b| lines_meet(*a, *b)))
        {
            pairs.insert((q.id.as_str(), 4u8));
        }
    }
    pairs
}

/// Radius rules are applied to the final, fully populated sheet. The existing route is the
/// fallback: a geometric improvement must also preserve the drawing's ink/contact budget.
#[allow(clippy::too_many_arguments)]
pub(crate) fn settle_radius_leaders(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    items: &mut [PlannedDimension],
    obstacles: &[PlannedDimension],
    cache: &super::footprints::Cache,
    reference: Option<&crate::model::Plan>,
) -> BTreeMap<String, Vec<Problem>> {
    let page = Page::new(analysis, sheet, views);
    let mut reports = BTreeMap::new();
    let incumbents = crate::placement_intent::held_dimensions(reference, views);
    let proofs = std::cell::OnceCell::new();
    for i in 0..items.len() {
        let d = &items[i];
        if d.kind != "callout"
            || spec
                .dimensions
                .get(&d.id)
                .is_some_and(|e| e.place.is_some())
        {
            continue;
        }
        let has = |p: &str| d.measurements.iter().any(|m| m.parameter == p);
        let parameter = if has("fillet.radius") {
            "fillet.radius"
        } else if has("cylindrical_profile.radius") {
            "cylindrical_profile.radius"
        } else if has("slot_end_radius.radius") {
            "slot_end_radius.radius"
        } else {
            "blend.radius"
        };
        let ids: Vec<_> = d
            .measurements
            .iter()
            .filter(|m| m.parameter == parameter)
            .map(|m| m.feature.as_str())
            .collect();
        if ids.is_empty() {
            continue;
        }
        let (Some(old), Some(v)) = (&d.placed, views.iter().find(|v| v.id == d.view)) else {
            continue;
        };
        let Some(&shaft) = old.lines.first() else {
            continue;
        };
        let frame = Frame::new(analysis, v);
        let mut arcs = Vec::new();
        for id in ids {
            if parameter == "slot_end_radius.radius" {
                arcs.extend(crate::radius::profile(analysis, id, v.direction, |q| {
                    frame.page(q)
                }));
            } else if let Ok(more) =
                crate::radius::projected(analysis, id, v.direction, |q| frame.page(q))
            {
                arcs.extend(more);
            }
        }
        // A compliant minimum-length route needs no search. Longer compliant
        // routes still compare both sides: accepting the first legal side can
        // otherwise retain a long shaft when the opposite side has clear paper.
        // Drawn lint audits retained routes without a duplicate plan problem.
        // Slot-end radius repairs have always required an exactly radial, outward
        // shaft. A newly populated radius inventory must not polish one to a 7.5°
        // near-radial alternative merely because general fillet lint permits it.
        let exact_slot = parameter == "slot_end_radius.radius";
        let accepts = |a: &crate::radius::Projected, [tip, elbow]: [P2; 2]| {
            let u = [tip[0] - a.centre[0], tip[1] - a.centre[1]];
            let v = [elbow[0] - tip[0], elbow[1] - tip[1]];
            let n = u[0].hypot(u[1]) * v[0].hypot(v[1]);
            a.accepts([tip, elbow])
                && (!exact_slot
                    || ((u[0] * v[1] - u[1] * v[0]).abs() <= 1e-9 * n
                        && u[0] * v[0] + u[1] * v[1] > 0.0))
        };
        let compliant = arcs.iter().any(|a| accepts(a, shaft));
        let centred = compliant && arcs.iter().any(|a| a.centred(shaft));
        if arcs.is_empty()
            || (reference.is_none()
                && centred
                && length(&old.lines) <= MIN_LEADER + SHOULDER + 1e-6)
        {
            continue;
        }
        let view = v.id.trim_start_matches("v:");
        let footprint = cache.get(analysis, v);
        let model = cache.model(analysis, v);
        let ink = label_ink(&d.terms, font());
        let job = Job {
            side: None,
            view,
            footprints: &footprint,
            outline: frame.outline(),
            area: page.area(view),
            sites: arcs
                .iter()
                .flat_map(|a| a.tips.iter().map(|p| (*p, 0.0, None)))
                .collect(),
            radial: false,
            silhouette: None,
            model: model.as_ref().map(|m| &m.as_ref().0),
            corridors: false,
            slot: false,
            directed: false,
            short_profile: false,
            inward: false,
            edge_attachment: true,
            interior_proof: proofs
                .get_or_init(|| super::interior::callout_proofs(analysis, views))
                .get(&v.id)
                .map(|p| (v, p.as_ref())),
            profile_proof: None,
            steer: std::cell::Cell::new(None),
            ink_min: ink.min,
            ink_max: ink.max,
            stroke_pad: stroke_clearance(&d.terms),
            reach: 0.0,
            beyond: [CLEAR; 4],
        };
        let others: Vec<_> = items
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .map(|(_, d)| d)
            .chain(obstacles)
            .collect();
        let mut fixed = Fixed::new(
            others
                .iter()
                .enumerate()
                .filter_map(|(j, d)| Some((j, d.placed.as_ref()?))),
        );
        // Final radius polishing sees the same centre-stroke semantics as the
        // main router. Treating those proxies as ordinary lines could introduce
        // a near-parallel run when a newly counted radius changes the inventory.
        fixed.furniture = others
            .iter()
            .enumerate()
            .filter(|(_, d)| d.kind == "ink")
            .map(|(j, d)| (j, d.basis == "global_axis"))
            .collect();
        fixed.crosses = others
            .iter()
            .enumerate()
            .filter(|(_, d)| d.kind == "ink" && d.basis == "cross")
            .map(|(j, _)| j)
            .collect();
        let end = old.lines.get(1).map_or(shaft[1], |s| s[1]);
        let before = measure_at(
            &job,
            shaft[0],
            shaft[1],
            end,
            end[0] >= shaft[1][0],
            &fixed,
            &page,
        );
        let prior_contacts = radius_contacts(old, others.iter().copied());
        // A different edit must not select another equally valid arc/corner.
        // Replay only unchanged claims/text in the same projection, and reject
        // the incumbent if the edit has put new ink in its corridor.
        if let Some(before) = incumbents.iter().find(|p| {
            p.id == d.id
                && p.view == d.view
                && p.terms == d.terms
                && p.measurements == d.measurements
        }) && reference.is_some_and(|r| {
            r.views.iter().any(|p| {
                p.id == v.id && p.scale == v.scale && p.direction == v.direction && p.up == v.up
            })
        }) && let Some(p) = &before.placed
            && (page.fits(p, view)
                || (before.region == Some(super::CandidateRegion::Interior)
                    && page.fits_interior(p, view)))
            && {
                // Compare against the published incumbent's contacts, not an
                // intermediate route that an unrelated edit happened to choose.
                // A retained noncompliant fallback keeps the same single lint
                // finding until its own intent or corridor changes.
                let mut allowed = radius_contacts(p, incumbents.iter().filter(|q| q.id != d.id));
                allowed.extend(
                    prior_contacts
                        .iter()
                        .copied()
                        .filter(|(id, _)| obstacles.iter().any(|q| q.id == *id)),
                );
                radius_contacts(p, others.iter().copied()).is_subset(&allowed)
            }
        {
            let route = route_reports_named(&d.id, &d.label, view, footprint.solids(), p.lines[0]);
            reports.insert(d.id.clone(), route);
            items[i].placed = Some(p.clone());
            items[i].region = before.region;
            items[i].basis.clone_from(&before.basis);
            continue;
        }
        if compliant && reference.is_some() {
            continue;
        }
        // Centred routes rank before merely compliant ones, then by score.
        let mut best: Option<((bool, i64), Candidate)> = None;
        if let Some(before) = &before {
            let model_budget = model_crossings(&job, before);
            // A new route may not run into more extension lines than the drawn one.
            let old_extensions = fixed.extension_index.contacts(&old.lines, old.label_box);
            // A centred route keeps within 2 mm of its length. Any other may be replaced by
            // a clear better route up to the longest leader allowed: a compliant one by a
            // centred one, a noncompliant one by any compliant one. Pointing radially at the
            // arc's middle outranks a shorter shaft (maintainer 2026-10-25).
            let length_budget = if centred {
                length(&old.lines) + 2.0 + 1e-6
            } else {
                MAX_LEADER + SHOULDER
            };
            for arc in &arcs {
                // Keep the current physical arc station available: a shallow
                // tag overlap may need only a sub-millimetre radial extension.
                for tip in arc
                    .tips
                    .iter()
                    .copied()
                    .chain(arc.on_arc(shaft[0]).then_some(shaft[0]))
                {
                    let u = arc.radial_direction(tip);
                    let n = u[0].hypot(u[1]);
                    if n <= 1e-12 {
                        continue;
                    }
                    for degrees in [
                        0.0f64, -7.5, 7.5, -15.0, 15.0, 180.0, 172.5, 187.5, 165.0, 195.0,
                    ] {
                        let (sin, cos) = degrees.to_radians().sin_cos();
                        let u = [(cos * u[0] - sin * u[1]) / n, (sin * u[0] + cos * u[1]) / n];
                        let exit = exit_distance(tip, u, job.outline);
                        for reach in SWEEP_REACH
                            .into_iter()
                            .chain(ESCAPES.map(|off| exit + off))
                            .chain((1..=4).map(|n| length(&[shaft]) + 0.5 * f64::from(n)))
                        {
                            let elbow = [tip[0] + reach * u[0], tip[1] + reach * u[1]];
                            // Every shoulder has this length. Reject over-budget reaches
                            // before measuring the much more expensive peer contacts.
                            if reach + SHOULDER > length_budget
                                || !accepts(arc, [tip, elbow])
                                || (compliant && !centred && !arc.centred([tip, elbow]))
                            {
                                continue;
                            }
                            let centred_c = arc.centred([tip, elbow]);
                            let right = elbow[0] >= tip[0];
                            let Some(mut c) = measure_bounds(&job, tip, elbow, right, &page) else {
                                continue;
                            };
                            // Unmeasured contact costs are nonnegative, so this lower bound
                            // cannot discard a candidate that would beat the current best.
                            if c.hard > before.hard
                                || (centred && c.score() >= before.score())
                                || best
                                    .as_ref()
                                    .is_some_and(|(k, _)| (!centred_c, c.score()) >= *k)
                            {
                                continue;
                            }
                            measure_fixed(&mut c, &job, &fixed);
                            c.measure_features(&job);
                            if c.hard > before.hard
                                || crate::material::reentry(footprint.solids(), [tip, elbow]) > 0.0
                                || c.crossings > before.crossings
                                || c.text > before.text
                                || c.clearance > before.clearance
                                || c.feature_crossings > before.feature_crossings
                                || c.beside > before.beside
                                || (centred && c.score() >= before.score())
                                || best
                                    .as_ref()
                                    .is_some_and(|(k, _)| (!centred_c, c.score()) >= *k)
                                || model_crossings(&job, &c) > model_budget
                                || {
                                    let e =
                                        fixed.extension_index.contacts(&c.segments(), c.label_box);
                                    e.0 > old_extensions.0 || e.1 > old_extensions.1
                                }
                                || !page.fits(&c.placed(), view)
                                || !radius_contacts(&c.placed(), others.iter().copied())
                                    .is_subset(&prior_contacts)
                            {
                                continue;
                            }
                            if best
                                .as_ref()
                                .is_none_or(|(k, _)| (!centred_c, c.score()) < *k)
                            {
                                best = Some(((!centred_c, c.score()), c));
                            }
                        }
                    }
                }
            }
        }
        let id = d.id.clone();
        if let Some((_, c)) = best {
            let route =
                route_reports_named(&id, &d.label, view, footprint.solids(), [c.tip, c.elbow]);
            items[i].placed = Some(c.placed());
            // A label inside the view was proven on blank face or in an opening by the job's
            // interior proof; any other moved label leaves an earlier proof behind.
            if boxes_overlap(c.label_box, job.outline) {
                items[i].region = Some(super::CandidateRegion::Interior);
                if !items[i].basis.contains(super::interior::BASIS) {
                    items[i].basis += super::interior::BASIS;
                }
            } else {
                items[i].region = None;
                items[i].basis = items[i].basis.replace(super::interior::BASIS, "");
            }
            reports.insert(id, route);
        }
    }
    reports
}

/// Finished-sheet quality using the same resolved ink and 2 mm run predicate
/// as callout refinement. This measures existing routes; it never repairs one.
pub(crate) fn sheet_route_limits(
    analysis: &crate::model::Analysis,
    plan: &crate::model::Plan,
) -> std::collections::BTreeSet<(String, Vec<String>)> {
    let key = serde_json::to_vec(&(&plan.views, &plan.dimensions))
        .expect("route quality inputs serialize");
    crate::planning::memo(analysis, "sheet-route-quality", key, || {
        let cache = super::footprints::Cache::default();
        let mut out = std::collections::BTreeSet::new();
        for view in &plan.views {
            let model = cache.model(analysis, view);
            let resolved = model
                .as_ref()
                .and_then(|ink| ink.0.inspection(analysis, view));
            let extensions: Vec<_> = plan
                .dimensions
                .iter()
                .filter(|d| {
                    plan.views
                        .iter()
                        .any(|v| v.id == d.view && v.sheet == view.sheet)
                })
                .filter_map(|d| d.placed.as_ref())
                .filter(|p| p.lines.len() >= 3 && p.arrows.len() == 2)
                .flat_map(|p| p.lines.iter().take(2).copied())
                .collect();
            for dimension in plan
                .dimensions
                .iter()
                .filter(|d| d.view == view.id && d.kind == "callout")
            {
                let Some(p) = &dimension.placed else { continue };
                if p.lines.is_empty() {
                    continue;
                }
                let drawn = resolved
                    .map(|ink| &ink.drawn)
                    .or_else(|| model.as_ref().map(|ink| &ink.0));
                if p.lines.iter().any(|line| {
                    drawn.is_some_and(|ink| ink.runs_beside(*line))
                        || runs_beside(*line, &extensions)
                }) {
                    out.insert(("leader_ink_run".into(), vec![dimension.id.clone()]));
                }
                if let Some(ink) = resolved {
                    let route = [p.lines[0], *p.lines.last().expect("nonempty leader")];
                    for crossing in 0..ink.hidden.route_crossings(route) {
                        out.insert((
                            "leader_hidden_crossing".into(),
                            vec![dimension.id.clone(), crossing.to_string()],
                        ));
                    }
                }
            }
        }
        out
    })
}

/// Local callout refinement after interior placement, with the rest of the ink held fixed
/// (maintainer decision (iii): leaders and their text keep off extension lines). A callout in
/// contact moves to the nearest station of its row with fewer contacts only when nothing a
/// draftsman sees is worse: no longer by over 2 mm, no new crossing of model, hidden-edge or
/// cutting-plane ink, no 2 mm run beside a model or extension line, text and arrowhead on no
/// new extension line, elbow and text off the outline, and no worse on every placement tier.
fn text_conflict(
    i: usize,
    p: &Placed,
    placed: &[Option<Placed>],
    obstacles: &[PlannedDimension],
) -> bool {
    placed
        .iter()
        .enumerate()
        .filter(|(j, _)| *j != i)
        .filter_map(|(_, p)| p.as_ref())
        .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
        .any(|q| {
            boxes_overlap(p.label_box, q.label_box)
                || super::placement::ink_through(p, q.label_box)
                || super::placement::ink_through(q, p.label_box)
        })
}

pub(super) fn edited_labels(spec: &crate::model::Spec) -> bool {
    spec.dimensions
        .values()
        .any(|d| d.tolerance.is_some() || d.text.is_some() || d.style.is_some())
        || spec.features.values().any(|f| f.fit.is_some())
}

/// Claim an established exterior route before the new witnesses settle. These
/// are trial preferences, not pins: the complete layout is still adjudicated,
/// interior placement and refinement may repair them, and the other schedules
/// can win if these corridors are unsuitable for the expanded inventory.
#[allow(clippy::too_many_arguments)]
pub(super) fn seed_established(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    views: &[crate::model::PlannedView],
    items: &[Want],
    chosen: &[Option<&str>],
    placed: &mut [Option<Placed>],
    page: &Page,
) -> bool {
    let native_corridors = items
        .iter()
        .flat_map(|w| &w.measurements)
        .any(|m| m.feature.starts_with("f:section_recess:"));
    let key = crate::model::established_key(spec);
    let nominal = nominal_reference(analysis, spec, items);
    let is_nominal = nominal.is_some();
    let Some(reference) = nominal.or_else(|| {
        crate::planning::lookup::<Result<crate::model::Plan, crate::model::Invalid>>(
            analysis,
            "established-drawing",
            &key,
        )
        .and_then(Result::ok)
    }) else {
        return false;
    };
    let mut seeded = false;
    for (i, w) in items.iter().enumerate() {
        if placed[i].is_some() || !matches!(w.shape, Shape::Callout { .. }) {
            continue;
        }
        if is_nominal
            && w.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        {
            continue;
        }
        let Some(d) = reference.dimensions.iter().find(|d| {
            d.id == w.id
                && d.kind == "callout"
                && d.measurements.iter().all(|m| w.measurements.contains(m))
        }) else {
            continue;
        };
        if reference.problems.iter().any(|p| {
            p.subjects.contains(&d.id)
                && (matches!(
                    p.code.as_str(),
                    "annotation_line_over_view" | "annotation_leader_long"
                ) || (!native_corridors
                    && matches!(
                        p.code.as_str(),
                        "annotation_leader_crossing"
                            | "annotation_ink_overlap"
                            | "annotation_overlap"
                            | "annotation_over_view"
                    )))
        }) {
            continue;
        }
        let p = if is_nominal {
            nominal_route(&reference, d, views, w)
        } else {
            translated(&reference, d, views, w)
        };
        let Some(p) = p else {
            continue;
        };
        if !crate::nominal::comparison()
            && w.measurements.iter().any(|m| {
                m.parameter == "slot_end_radius.radius"
                    && crate::nominal::format(m.value)
                        .split_once('.')
                        .is_some_and(|(_, tail)| tail.len() > 1)
            })
            && let Shape::Callout { sites, arcs } = &w.shape
            && let Some(v) = views.iter().find(|v| v.id == d.view)
            && !radial_shaft(
                &page_sites(&Frame::new(analysis, v), sites, arcs),
                p.lines[0][0],
                p.lines[0][1],
            )
        {
            continue;
        }
        if chosen[i].is_some_and(|v| d.view == format!("v:{v}") && page.fits(&p, v)) {
            placed[i] = Some(p);
            seeded = true;
        }
    }
    seeded
}

fn translated(
    reference: &crate::model::Plan,
    d: &PlannedDimension,
    views: &[crate::model::PlannedView],
    w: &Want,
) -> Option<Placed> {
    let before = reference.views.iter().find(|v| v.id == d.view)?;
    let now = views.iter().find(|v| {
        v.id == d.view
            && v.scale == before.scale
            && v.direction == before.direction
            && v.up == before.up
    })?;
    let mut p = super::placement::relabel(d.placed.as_ref()?, &d.terms, w);
    let delta = [now.at[0] - before.at[0], now.at[1] - before.at[1]];
    for q in p.lines.iter_mut().chain(&mut p.arrows).flatten() {
        q[0] += delta[0];
        q[1] += delta[1];
    }
    for (k, shift) in delta.into_iter().enumerate() {
        p.label_at[k] += shift;
        p.label_box[k] += shift;
        p.label_box[k + 2] += shift;
    }
    Some(p)
}

fn nominal_route(
    before: &crate::model::Plan,
    d: &PlannedDimension,
    views: &[crate::model::PlannedView],
    w: &Want,
) -> Option<Placed> {
    let old = before.views.iter().find(|v| v.id == d.view)?;
    let now = views.iter().find(|v| {
        v.id == d.view
            && v.direction == old.direction
            && v.up == old.up
            && v.scale == old.scale
            && v.cut == old.cut
            && v.detail == old.detail
    })?;
    let mut p = super::placement::relabel(d.placed.as_ref()?, &d.terms, w);
    // The shoulder is a paper leader, not a width measurement of the text.
    // Keep it short when the accurately printed nominal gains digits.
    p.lines = d.placed.as_ref()?.lines.clone();
    let [tip_at, elbow_at] = *p.lines.first()?;
    let [_, end_at] = *p.lines.last()?;
    if (elbow_at[0] - tip_at[0]) * (end_at[0] - elbow_at[0]) < -1e-6 {
        return None;
    }
    let delta = [0, 1].map(|k| now.at[k] - old.at[k]);
    for q in p.lines.iter_mut().chain(&mut p.arrows).flatten() {
        crate::placement_intent::move_point(q, delta);
    }
    for (k, shift) in delta.into_iter().enumerate() {
        p.label_at[k] += shift;
        p.label_box[k] += shift;
        p.label_box[k + 2] += shift;
    }
    Some(p)
}

// Exact refinement rank, movement and immutable route geometry.
type RouteStation = ((usize, usize), f64, P2, P2, P2, bool, [f64; 4]);

#[allow(clippy::too_many_arguments)]
pub(super) fn refine(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    cache: &super::footprints::Cache,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&str>],
    placed: &mut [Option<Placed>],
    page: &Page,
    obstacles: &[PlannedDimension],
    proofs: &BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
) -> BTreeMap<String, Vec<Problem>> {
    if analysis.edge_ink.is_none() {
        return BTreeMap::new();
    }
    let reference_key = crate::model::established_key(spec);
    // The reference is immutable for this key within the Analysis scope.
    let has_reference =
        crate::planning::lookup::<Result<crate::model::Plan, crate::model::Invalid>>(
            analysis,
            "established-drawing",
            &reference_key,
        )
        .is_some();
    let frame_geometry: Vec<_> = frames
        .iter()
        .map(|(name, f)| (name, f.memo_geometry()))
        .collect();
    let canonical = crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
        .unwrap_or(false);
    let wrapped_polish =
        crate::planning::lookup::<bool>(analysis, "wrapped-route-polish", &[]).unwrap_or(false);
    let proof_views: Vec<_> = proofs.keys().collect();
    let mut geometry_sheet = sheet.clone();
    geometry_sheet.title.clear();
    geometry_sheet.title_cells.clear();
    geometry_sheet.basis.clear();
    geometry_sheet.arrangement.clear();
    geometry_sheet.default = false;
    geometry_sheet.fill = 0.0;
    let mut geometry_views = views.to_vec();
    for view in &mut geometry_views {
        view.basis.clear();
    }
    let key = serde_json::to_vec(&(
        &spec.dimensions,
        edited_labels(spec),
        proof_views,
        reference_key,
        has_reference,
        geometry_sheet,
        geometry_views,
        frame_geometry,
        canonical,
        wrapped_polish,
        items,
        views_of,
        &*placed,
        page,
        obstacles,
    ))
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("leader refinement inputs serialize");
    let (next, reports) = crate::planning::memo(analysis, "settled-leader-refinement", key, || {
        let mut next = placed.to_vec();
        let reports = refine_uncached(
            analysis, spec, cache, sheet, views, frames, items, views_of, &mut next, page,
            obstacles, proofs,
        );
        (next, reports)
    });
    placed.clone_from_slice(&next);
    reports
}

#[allow(clippy::too_many_arguments)]
fn refine_uncached(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    cache: &super::footprints::Cache,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&str>],
    placed: &mut [Option<Placed>],
    page: &Page,
    obstacles: &[PlannedDimension],
    proofs: &BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
) -> BTreeMap<String, Vec<Problem>> {
    let mut updated = BTreeMap::new();
    let Some(_) = analysis.edge_ink.as_ref() else {
        return updated;
    };
    // The linear dimensions' extension lines, each with its owner. Refinement moves only
    // callouts, so these stay fixed throughout.
    let extensions: Vec<(usize, [P2; 2])> = placed
        .iter()
        .enumerate()
        .filter_map(|(j, p)| p.as_ref().map(|p| (j, p)))
        .filter(|(_, p)| p.arrows.len() == 2 && p.lines.len() >= 3)
        .flat_map(|(j, p)| p.lines[..2].iter().map(move |s| (j, *s)))
        .collect();
    let extension_index = ExtensionIndex::new(extensions.clone());
    // Runs beside newly recognised edges need the same bounded repair as extension
    // contacts. Gather each projection once, then test only routes that need repair.
    let thread_callouts = items.iter().any(|w| {
        w.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:modelled_thread:"))
    });
    let repair_models: BTreeMap<_, _> = if thread_callouts
        || items.iter().any(|w| {
            w.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        }) {
        views
            .iter()
            .filter_map(|v| Some((v.id.trim_start_matches("v:"), cache.model(analysis, v)?)))
            .collect()
    } else {
        BTreeMap::new()
    };
    let extension_ink: Vec<_> = extensions.iter().map(|(_, e)| *e).collect();
    let bad_run = |p: &Placed, view: &str| {
        repair_models.get(view).is_some_and(|model| {
            p.lines
                .iter()
                .any(|s| model.as_ref().0.runs_beside(*s) || runs_beside(*s, &extension_ink))
        })
    };
    let centre_lines = |lines: &[[P2; 2]]| {
        lines.iter().any(|line| {
            obstacles
                .iter()
                .filter(|d| d.kind == "ink")
                .filter_map(|d| d.placed.as_ref())
                .flat_map(|p| &p.lines)
                .any(|fixed| crate::centrelines::runs_beside(*line, *fixed))
        })
    };
    let centre_run = |p: &Placed| centre_lines(&p.lines);
    let reference_key = crate::model::established_key(spec);
    let established_reference = crate::planning::lookup::<
        Result<crate::model::Plan, crate::model::Invalid>,
    >(analysis, "established-drawing", &reference_key)
    .and_then(Result::ok);
    let bare = established_reference.is_none();
    // Bare API plans do not run sheet recovery. Give their final callout repair
    // an established same-scale recipe without changing the lane solve or its
    // inventory. The final drawing always uses the full original Analysis.
    let reference = nominal_reference(analysis, spec, items)
        .or(established_reference)
        .or_else(|| {
            if !items.iter().any(|w| {
                w.measurements
                    .iter()
                    .any(|m| m.feature.starts_with("f:section_recess:"))
            }) || obstacles.iter().any(|d| d.kind == "callout")
            {
                return None;
            }
            let key = serde_json::to_vec(&(
                &sheet.size,
                &sheet.scale,
                &spec.sheet.projection,
                spec.sheet.references,
            ))
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            .expect("bare route inputs serialize");
            // A route template: the established normal-spacing drawing at this paper and
            // scale, whatever the candidate's dimension spacing.
            crate::dims::with_compact(false, || {
                crate::planning::memo(analysis, "bare-leader-reference", key, || {
                    let base = crate::lint::slots::without_recesses(analysis);
                    let mut recipe = crate::model::Spec::default();
                    recipe.sheet.size = Some(sheet.size.clone());
                    recipe.sheet.scale = Some(sheet.scale.clone());
                    recipe.sheet.projection = spec.sheet.projection.clone();
                    recipe.sheet.references = spec.sheet.references;
                    crate::model::plan(&base, &recipe).ok()
                })
            })
        });
    let legacy = |i: usize| {
        reference.as_ref().and_then(|p| {
            p.dimensions.iter().find(|d| {
                d.id == items[i].id
                    && d.kind == "callout"
                    && d.view.strip_prefix("v:") == views_of[i]
                    && d.measurements
                        .iter()
                        .all(|m| items[i].measurements.contains(m))
            })
        })
    };
    let changed_from_established = |i: usize, p: &Placed| {
        legacy(i)
            .and_then(|d| translated(reference.as_ref()?, d, views, &items[i]))
            .is_some_and(|old| {
                old.lines.len() == p.lines.len()
                    && old.lines.iter().zip(&p.lines).any(|(a, b)| {
                        a.iter()
                            .zip(b)
                            .any(|(a, b)| (0..2).any(|k| (a[k] - b[k]).abs() > 1e-6))
                    })
            })
    };
    let longer_than_established = |i: usize, p: &Placed| {
        legacy(i)
            .and_then(|d| d.placed.as_ref())
            .is_some_and(|old| length(&p.lines) > length(&old.lines) + 2.0 + 1e-6)
    };
    // A joint assignment can shorten an old leader while sending it through
    // different model edges. Length and annotation contacts alone do not catch
    // that regression; compare the complete physical route with its translated
    // established recipe before deciding whether refinement is necessary.
    let added_material_crossing = |i: usize, p: &Placed| {
        legacy(i)
            .and_then(|d| translated(reference.as_ref()?, d, views, &items[i]))
            .zip(views_of[i].and_then(|view| repair_models.get(view)))
            .is_some_and(|(old, model)| model.as_ref().0.new_crossing(&p.lines, &old.lines))
    };
    let callout_crossing = |i: usize, p: &Placed, placed: &[Option<Placed>]| {
        bare && !repair_models.is_empty()
            && obstacles.iter().all(|d| d.kind != "callout")
            && placed.iter().enumerate().any(|(j, q)| {
                i != j
                    && q.as_ref().is_some_and(|q| {
                        q.arrows.len() == 1
                            && p.lines.iter().any(|s| {
                                q.lines.iter().chain(&q.arrows).any(|t| lines_meet(*s, *t))
                            })
                    })
            })
    };
    // Each view's line-work for the route gates, gathered only once a candidate reaches it.
    let view_reach: Vec<[f64; 4]> = views
        .iter()
        .map(|v| {
            let b = if v.id == "v:iso" {
                let b = analysis.iso_bounds;
                [
                    v.at[0] + v.scale * b[0],
                    v.at[1] + v.scale * b[1],
                    v.at[0] + v.scale * b[2],
                    v.at[1] + v.scale * b[3],
                ]
            } else {
                Frame::new(analysis, v).outline()
            };
            grow(b, 1.0)
        })
        .collect();
    let gathered: Vec<_> = views
        .iter()
        .map(|v| {
            std::cell::LazyCell::new(move || {
                // Invariant: The projection cache was populated for these planned views before routing.
                cache.model(analysis, v).expect("edge ink is available")
            })
        })
        .collect();
    // Every route lies inside its candidate union plus two text pads. The same
    // eligible cameras therefore supply the exact same full edge predicates;
    // edges outside that union cannot touch a query. Share their trees and memo
    // queries across callouts instead of rebuilding a cropped tree for each one.
    let mut route_ink: BTreeMap<Vec<usize>, std::rc::Rc<RefinementInk>> = BTreeMap::new();
    let canonical = crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
        .unwrap_or(false);
    let model_run = |w: &Want, p: &Placed| {
        // The comparison recipe retains its original routes. Main's new inspection
        // repair belongs to the final drawing, which is checked against that recipe.
        if canonical {
            return false;
        }
        let label = label_ink(&w.terms, font());
        let span = ROW * STEP
            + length(&p.lines)
            + 2.0
            + (label.max[0] - label.min[0])
            + (label.max[1] - label.min[1])
            + 4.0 * LABEL_PAD;
        let elbow = p.lines[0][1];
        let probe = grow([elbow[0], elbow[1], elbow[0], elbow[1]], span);
        let chamfer = w
            .measurements
            .iter()
            .any(|m| m.parameter == "chamfer.length");
        p.lines.iter().any(|s| runs_beside(*s, &extension_ink))
            || views.iter().enumerate().any(|(k, _)| {
                if !boxes_meet(view_reach[k], probe) {
                    return false;
                }
                let (model, uncertain) = gathered[k].as_ref();
                p.lines.iter().any(|s| model.runs_beside(*s))
                    || (chamfer
                        && uncertain
                            .iter()
                            .any(|b| p.lines.iter().any(|s| buried(*s, *b) > 2.0)))
            })
    };
    // Only a free callout already in contact is refined; most sheets have none, and then the
    // view ink below is never gathered.
    let footprints: BTreeMap<_, _> = views
        .iter()
        .map(|v| (v.id.trim_start_matches("v:"), cache.get(analysis, v)))
        .collect();
    let expanded_native = super::has_new_native_recess_dimensions(analysis) || thread_callouts;
    let feature_route = |i: usize, p: &Placed| {
        expanded_native
            && !repair_models.is_empty()
            && items[i]
                .measurements
                .iter()
                .any(|m| m.parameter == "bore.diameter")
            && views_of[i].is_some_and(|v| {
                !footprints[v]
                    .crossed(p.lines[0], &items[i].measurements)
                    .is_empty()
            })
    };
    let wrapped_polish =
        crate::planning::lookup::<bool>(analysis, "wrapped-route-polish", &[]).unwrap_or(false);
    let edited_text = edited_labels(spec);
    let todo: Vec<usize> = (0..items.len())
        .filter(|&i| {
            matches!(items[i].shape, Shape::Callout { .. })
                && views_of[i].is_some()
                && !spec
                    .dimensions
                    .get(&items[i].id)
                    .is_some_and(|d| d.place.is_some())
                && placed[i].as_ref().is_some_and(|p| {
                    extension_index.contacts(&p.lines, p.label_box).0 > 0
                        || feature_route(i, p)
                        || (expanded_native
                            && !repair_models.is_empty()
                            && !bare
                            && !canonical
                            && !edited_text
                            && changed_from_established(i, p))
                        || callout_crossing(i, p, placed)
                        || longer_than_established(i, p)
                        || (wrapped_polish
                            && !canonical
                            && items[i].terms.iter().any(|s| crate::callouts::is_break(s)))
                        || added_material_crossing(i, p)
                        // Invariant: This branch handles only routed callouts, whose view was selected above.
                        || bad_run(p, views_of[i].unwrap())
                        || model_run(&items[i], p)
                        || (edited_text && text_conflict(i, p, placed, obstacles))
                        || (items[i]
                            .measurements
                            .iter()
                            .any(|m| m.feature.starts_with("f:section_recess:"))
                            && (centre_run(p) || text_conflict(i, p, placed, obstacles)))
                })
        })
        .collect();
    if todo.is_empty() {
        return updated;
    }
    let cuts: Vec<_> = views
        .iter()
        .flat_map(|v| &v.line_work)
        .filter(|w| w.kind == "cutting_plane")
        .flat_map(crate::centrelines::segments)
        .collect();
    let directed = !repair_models.is_empty() && !has_semantic_pmi(analysis, frames);
    for i in todo {
        let w = &items[i];
        let (Shape::Callout { sites, arcs }, Some(view), Some(old)) =
            (&w.shape, views_of[i], placed[i].clone())
        else {
            continue;
        };
        let [tip, elbow] = old.lines[0];
        let end = old.lines[1][1];
        let right = end[0] >= elbow[0];
        let frame = &frames[view];
        let page_sites = page_sites(frame, sites, arcs);
        let old_length = length(&old.lines);
        let wrapped =
            wrapped_polish && !canonical && w.terms.iter().any(|s| crate::callouts::is_break(s));
        let new_recess = w
            .measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"));
        let ceiling = legacy(i)
            .and_then(|d| d.placed.as_ref())
            .map(|p| length(&p.lines) + 2.0);
        let max_length = ceiling.unwrap_or(if new_recess {
            MAX_LEADER
        } else {
            old_length + 2.0
        });
        let repair_text = (new_recess || edited_text) && text_conflict(i, &old, placed, obstacles);
        // A longer authored label may overlap a peer while its shaft is clear.
        // Include slides along that exact shaft, preserving its real attachment
        // and the normal material, length and witness gates.
        let established = legacy(i)
            .and_then(|d| nominal_route(reference.as_ref()?, d, views, w))
            .or_else(|| (edited_text && repair_text).then(|| old.clone()));
        let clear_new_text = repair_text
            && !w.measurements.is_empty()
            && w.measurements
                .iter()
                .all(|m| m.feature.starts_with("f:section_recess:"));
        let repair_cross = callout_crossing(i, &old, placed);
        let old_run = model_run(w, &old);
        let repair_features = feature_route(i, &old);
        let restore_established = expanded_native
            && !repair_models.is_empty()
            && !bare
            && !canonical
            && !edited_text
            && changed_from_established(i, &old);
        let repair_run = old_run
            || wrapped
            || restore_established
            || repair_features
            || repair_cross
            || repair_text
            || longer_than_established(i, &old)
            || added_material_crossing(i, &old)
            || bad_run(&old, view)
            || (w
                .measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
                && centre_run(&old));
        let label = label_ink(&w.terms, font());
        let before = extension_index.contacts(&old.lines, old.label_box);
        // Every station of the local row, shoulder-only changes first, kept only while it is
        // no longer than the old route by 2 mm and lowers the contacts without the text gaining
        // one: text on an extension line is worse than a leader across it.
        let job = Job {
            side: spec
                .dimensions
                .get(&w.id)
                .and_then(|e| e.place.as_ref())
                .and_then(crate::placement_intent::side_hint),
            stroke_pad: stroke_clearance(&w.terms),
            reach: own_reach(&w.measurements, frame.scale()),
            view,
            footprints: &footprints[view],
            outline: frame.outline(),
            area: page.area(view),
            sites: page_sites.clone(),
            radial: !crate::nominal::comparison()
                && w.measurements.iter().any(|m| {
                    m.parameter == "slot_end_radius.radius"
                        && crate::nominal::format(m.value)
                            .split_once('.')
                            .is_some_and(|(_, tail)| tail.len() > 1)
                }),
            silhouette: grazing(frame, w),
            model: None,
            short_profile: boss_profile(w),
            inward: false,
            edge_attachment: true,
            interior_proof: None,
            profile_proof: boss_profile(w)
                .then(|| {
                    let v = views
                        .iter()
                        .find(|v| v.id.trim_start_matches("v:") == view)?;
                    Some((v, proofs.get(&v.id)?.as_ref()))
                })
                .flatten(),
            directed,
            slot: slot_callout(w),
            corridors: boss_profile(w)
                || w.measurements
                    .iter()
                    .any(|m| m.feature.starts_with("f:section_recess:")),
            steer: std::cell::Cell::new(None),
            ink_min: label.min,
            ink_max: label.max,
            beyond: [CLEAR; 4],
        };
        let mut candidates = Vec::new();
        let reach = (if old_run { 3.0 * ROW } else { ROW }) as i32;
        let shifts = std::iter::once([0, 0]).chain((-reach..=reach).flat_map(|x| {
            (-reach..=reach)
                .filter(move |y| x != 0 || *y != 0)
                .map(move |y| [x, y])
        }));
        let shelf = (end[0] - elbow[0]).abs();
        let mut elbows: Vec<P2> = shifts
            .map(|shift| [0, 1].map(|k| elbow[k] + f64::from(shift[k]) * STEP))
            .collect();
        if super::turned::axial_datum(analysis).is_some_and(|(k, _)| k == 2) {
            // A new inspection baseline can occupy the old diameter column. The opposite
            // silhouette is the same diameter; try its mirrored column under every existing
            // length, feature, arrowhead and text-clearance gate.
            let outline = frame.outline();
            let mirror = outline[0] + outline[2] - elbow[0];
            elbows.extend((-reach..=reach).map(|dy| [mirror, elbow[1] + f64::from(dy) * STEP]));
        }
        for q in elbows {
            let t = if q == elbow {
                tip
            } else {
                let site = nearest(&page_sites, q);
                if (q[0] - site.0[0]).hypot(q[1] - site.0[1]) <= site.1 {
                    continue;
                }
                rim_tip(site, q)
            };
            let shaft = (q[0] - t[0]).hypot(q[1] - t[1]);
            if shaft < MIN_LEADER || shaft + shelf > old_length + 2.0 + 1e-5 {
                continue;
            }
            for right in [right, !right] {
                // A shoulder never shrinks below the natural one: a stub reads as part of
                // whatever line the elbow sits near.
                for shoulder in [shelf, shelf + STEP, shelf + 2.0 * STEP] {
                    let e = [q[0] + if right { shoulder } else { -shoulder }, q[1]];
                    if (q[0] - t[0]) * (e[0] - q[0]) < -1e-9 {
                        continue;
                    }
                    let lines = [[t, q], [q, e]];
                    if length(&lines) > max_length + 1e-6 {
                        continue;
                    }
                    let (_, block) = label_geometry(q, e, right, label.min, label.max);
                    // Keep the initial inventory intact. Only final refinement
                    // tries a small glyph lift when a tall wrapped block barely
                    // misses the nearest clear route; every physical gate below
                    // compares it with the already settled drawing.
                    for lift in
                        std::iter::once(0.0).chain(wrapped.then_some(0.25 * (q[1] - t[1]).signum()))
                    {
                        let lb = [block[0], block[1] + lift, block[2], block[3] + lift];
                        let Some(now) = extension_index.contacts_bounded(&lines, lb, Some(before))
                        else {
                            continue;
                        };
                        if now.0 > before.0
                            || (!repair_run && now.0 == before.0)
                            || now.1 > before.1
                        {
                            continue;
                        }
                        let moved = (q[0] - elbow[0]).hypot(q[1] - elbow[1])
                            + (e[0] - end[0]).hypot(e[1] - end[1])
                            + lift.abs();
                        candidates.push((now, moved, t, q, e, right, lb));
                    }
                }
            }
        }
        if wrapped {
            // Shorten along the settled shaft itself. Its true attachment and
            // every crossed model edge stay fixed, unlike a new radial route.
            let shaft = (elbow[0] - tip[0]).hypot(elbow[1] - tip[1]);
            let steps = ((shaft - MIN_LEADER).min(3.0 * ROW) / 0.25).floor() as i32;
            for step in 1..=steps {
                let f = (shaft - f64::from(step) * 0.25) / shaft;
                let q = [0, 1].map(|k| tip[k] + f * (elbow[k] - tip[k]));
                let e = [q[0] + if right { shelf } else { -shelf }, q[1]];
                let lines = [[tip, q], [q, e]];
                if length(&lines) > max_length + 1e-6 {
                    continue;
                }
                let (_, block) = label_geometry(q, e, right, label.min, label.max);
                for lift in [0.0, 0.25 * (q[1] - tip[1]).signum()] {
                    let lb = [block[0], block[1] + lift, block[2], block[3] + lift];
                    let Some(now) = extension_index.contacts_bounded(&lines, lb, Some(before))
                    else {
                        continue;
                    };
                    if now.0 <= before.0 && now.1 <= before.1 {
                        let moved = (q[0] - elbow[0]).hypot(q[1] - elbow[1])
                            + (e[0] - end[0]).hypot(e[1] - end[1])
                            + lift.abs();
                        candidates.push((now, moved, tip, q, e, right, lb));
                    }
                }
            }
        }
        if let Some(p) = established
            .as_ref()
            .filter(|p| p.lines.len() == 2 && length(&p.lines) <= max_length + 1e-6)
        {
            let [t, q] = p.lines[0];
            let e = p.lines[1][1];
            let right = e[0] >= q[0];
            let (_, lb) = label_geometry(q, e, right, label.min, label.max);
            let now = extension_index.contacts(&p.lines, lb);
            // Extend or trim the established shaft along its exact supporting
            // line, then keep the standard horizontal shelf. Only ink outside
            // the uncertain material can be new; the route gate proves this.
            if repair_run {
                let dy = q[1] - t[1];
                if dy.abs() > 1e-9 {
                    for step in -28..=28 {
                        let y = q[1] + f64::from(step) * 0.25;
                        let q = [t[0] + (q[0] - t[0]) * (y - t[1]) / dy, y];
                        for shoulder in [0.75, 1.5, SHOULDER] {
                            let e = [q[0] + if right { shoulder } else { -shoulder }, y];
                            let lines = [[t, q], [q, e]];
                            if self::length(&lines) > max_length + 1e-6
                                || (q[0] - t[0]) * (e[0] - q[0]) < -1e-9
                            {
                                continue;
                            }
                            let (_, lb) = label_geometry(q, e, right, label.min, label.max);
                            let Some(contacts) =
                                extension_index.contacts_bounded(&lines, lb, Some(before))
                            else {
                                continue;
                            };
                            if contacts.0 <= before.0 && contacts.1 <= before.1 {
                                let moved = (q[0] - elbow[0]).hypot(q[1] - elbow[1])
                                    + (e[0] - end[0]).hypot(e[1] - end[1]);
                                candidates.push((contacts, moved, t, q, e, right, lb));
                            }
                        }
                    }
                }
            }
            if now.0 <= before.0 && now.1 <= before.1 && (repair_run || now.0 < before.0) {
                let moved =
                    (q[0] - elbow[0]).hypot(q[1] - elbow[1]) + (e[0] - end[0]).hypot(e[1] - end[1]);
                candidates.push((now, moved, t, q, e, right, lb));
                // An established shaft may already be the clearest route, with
                // its glyph only a fraction short of the outline's text pad.
                // Keep the shaft and shelf exact; try a small glyph lift with
                // real baseline and bounding-box movement, never a fake box.
                for lift in [0.25, -0.25, 0.5, -0.5] {
                    let lifted = [lb[0], lb[1] + lift, lb[2], lb[3] + lift];
                    let contacts = extension_index.contacts(&p.lines, lifted);
                    if contacts.0 <= before.0 && contacts.1 <= before.1 {
                        candidates.push((contacts, moved + lift.abs(), t, q, e, right, lifted));
                    }
                }
            }
        }
        // Local row moves cannot reach the opposite wall of a small pocket
        // or the opposite rim of a hole. Retry the same bounded radial sweep
        // used by the initial solver, retaining the route-length and ink gates.
        for (t, q, right) in (!repair_models.is_empty())
            .then(|| sweep(&job, tip))
            .into_iter()
            .flatten()
        {
            let e = [q[0] + if right { SHOULDER } else { -SHOULDER }, q[1]];
            let lines = [[t, q], [q, e]];
            if length(&lines) > max_length + 1e-6 {
                continue;
            }
            let (_, lb) = label_geometry(q, e, right, label.min, label.max);
            let Some(now) = extension_index.contacts_bounded(&lines, lb, Some(before)) else {
                continue;
            };
            if now.0 > before.0 || (!repair_run && now.0 == before.0) || now.1 > before.1 {
                continue;
            }
            let moved =
                (q[0] - elbow[0]).hypot(q[1] - elbow[1]) + (e[0] - end[0]).hypot(e[1] - end[1]);
            candidates.push((now, moved, t, q, e, right, lb));
        }
        if clear_new_text {
            // A dense datum tower can leave only a narrow clear row. The radial
            // sweep's discrete reaches may skip it, so also use the initial
            // solver's bounded one-millimetre stations beside the view.
            let o = job.outline;
            let dr = page.drawable();
            for right in [true, false] {
                for off in ESCAPES {
                    let x = if right { o[2] + off } else { o[0] - off };
                    for y in stations(tip[1], o[1].max(dr[1]), o[3].min(dr[3])) {
                        let q = [x, y];
                        let t = rim_tip(nearest(&page_sites, q), q);
                        let e = [x + if right { SHOULDER } else { -SHOULDER }, y];
                        let lines = [[t, q], [q, e]];
                        if length(&lines) > max_length + 1e-6
                            || (q[0] - t[0]).hypot(q[1] - t[1]) < MIN_LEADER
                        {
                            continue;
                        }
                        let (_, lb) = label_geometry(q, e, right, label.min, label.max);
                        let Some(now) = extension_index.contacts_bounded(&lines, lb, Some(before))
                        else {
                            continue;
                        };
                        if now.0 > before.0 || now.1 > before.1 {
                            continue;
                        }
                        let moved = (q[0] - elbow[0]).hypot(q[1] - elbow[1])
                            + (e[0] - end[0]).hypot(e[1] - end[1]);
                        candidates.push((now, moved, t, q, e, right, lb));
                    }
                }
            }
        }
        if repair_cross {
            // Concentric diameter callouts can need a fan on opposite sides of
            // their common axis. Reflect the elbow about its real circular site;
            // the route keeps its established length bound and all ink gates.
            let site = nearest(&page_sites, elbow);
            if site.1 > 0.0 {
                for shift in -(ROW as i32)..=ROW as i32 {
                    let q = [2.0 * site.0[0] - elbow[0] + f64::from(shift), elbow[1]];
                    let t = rim_tip(site, q);
                    for right in [true, false] {
                        let shoulder = (end[0] - elbow[0]).abs();
                        let e = [q[0] + if right { shoulder } else { -shoulder }, q[1]];
                        let lines = [[t, q], [q, e]];
                        let (_, lb) = label_geometry(q, e, right, label.min, label.max);
                        let Some(now) = extension_index.contacts_bounded(&lines, lb, Some(before))
                        else {
                            continue;
                        };
                        if length(&lines) <= max_length + 1e-6
                            && now.0 <= before.0
                            && now.1 <= before.1
                        {
                            let moved = (q[0] - elbow[0]).hypot(q[1] - elbow[1])
                                + (e[0] - end[0]).hypot(e[1] - end[1]);
                            candidates.push((now, moved, t, q, e, right, lb));
                        }
                    }
                }
            }
        }
        if candidates.is_empty() {
            continue;
        }
        // The line-work near the candidates, so each one is tested against only what it can meet.
        let mut region = old.label_box;
        for (_, _, t, q, e, _, lb) in &candidates {
            for p in [*t, *q, *e, [lb[0], lb[1]], [lb[2], lb[3]]] {
                region = [
                    region[0].min(p[0]),
                    region[1].min(p[1]),
                    region[2].max(p[0]),
                    region[3].max(p[1]),
                ];
            }
        }
        for p in old.lines.iter().flatten() {
            region = [
                region[0].min(p[0]),
                region[1].min(p[1]),
                region[2].max(p[0]),
                region[3].max(p[1]),
            ];
        }
        let region = [
            region[0] - 2.0 * LABEL_PAD,
            region[1] - 2.0 * LABEL_PAD,
            region[2] + 2.0 * LABEL_PAD,
            region[3] + 2.0 * LABEL_PAD,
        ];
        let selected: Vec<_> = view_reach
            .iter()
            .enumerate()
            .filter(|(_, reach)| boxes_meet(**reach, region))
            .map(|(k, _)| k)
            .collect();
        let prepared = route_ink
            .entry(selected.clone())
            .or_insert_with(|| {
                let mut model = Vec::new();
                let mut unknown = Vec::new();
                for k in selected {
                    let (edges, boxes) = gathered[k].as_ref();
                    edges.append_ink(&mut model);
                    unknown.extend(boxes);
                }
                let lines = model
                    .iter()
                    .copied()
                    .chain(extension_ink.iter().copied())
                    .collect();
                let unknown_grid = Grid::new([unknown.clone(), Vec::new(), Vec::new()]);
                std::rc::Rc::new((
                    SegmentIndex::new(model),
                    SegmentIndex::new(lines),
                    unknown,
                    unknown_grid,
                ))
            })
            .clone();
        let (model_index, line_index, uncertain, unknown_grid) = prepared.as_ref();
        let near_cuts: Vec<_> = cuts
            .iter()
            .filter(|s| touches_box(**s, region))
            .copied()
            .collect();
        let crossed = |lines: &[[P2; 2]], e: [P2; 2]| lines.iter().any(|s| segments_cross(*s, e));
        // The text and the elbow keep off the outline: a label or an elbow within the label
        // pad of a model line reads as butting the part.
        let on_model = |q: P2, lb: [f64; 4]| {
            (
                model_index.touching(grow(lb, LABEL_PAD)),
                model_index.touching(grow([q[0], q[1], q[0], q[1]], LABEL_PAD)) > 0,
            )
        };
        let (old_text_model, old_elbow_model) = on_model(elbow, old.label_box);
        // The arrowhead keeps out of the extension lines it was clear of: a flare over the
        // start of one reads as a contact whatever the shaft does.
        let arrow_contacts = std::cell::RefCell::new(std::collections::HashMap::new());
        let head_on = |t: P2, q: P2| {
            let key = [
                t[0].to_bits(),
                t[1].to_bits(),
                q[0].to_bits(),
                q[1].to_bits(),
            ];
            if let Some(&count) = arrow_contacts.borrow().get(&key) {
                return count;
            }
            let count = extension_index.head_contacts(&head(arrow(t, q)));
            arrow_contacts.borrow_mut().insert(key, count);
            count
        };
        let old_head = head_on(tip, elbow);
        let historical = established
            .as_ref()
            .map_or(old.lines.as_slice(), |p| p.lines.as_slice());
        let route_safe = |lines: &[[P2; 2]]| {
            (clear_new_text
                || unknown_route_safe(
                    lines,
                    uncertain,
                    unknown_grid,
                    historical,
                    &old.lines,
                    old_run && repair_models.is_empty() && !edited_text,
                ))
                && !lines.iter().any(|s| line_index.runs_beside(*s))
                && (clear_new_text || !model_index.new_crossing(lines, historical))
                && !near_cuts
                    .iter()
                    .any(|s| crossed(lines, *s) && !crossed(historical, *s))
        };

        // The incumbent and its measured roles stay fixed for every candidate.
        let keep_off_centres = centre_run(&old)
            && w.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"));
        let route_allowed = |(_, _, t, q, e, right, lb): &RouteStation| {
            if clear_new_text {
                let (label_at, _) = label_geometry(*q, *e, *right, label.min, label.max);
                let p = Placed {
                    lines: vec![[*t, *q], [*q, *e]],
                    arrows: vec![arrow(*t, *q)],
                    label_at,
                    label_box: *lb,
                    rotation: 0.0,
                };
                if !page.fits(&p, view)
                    || text_conflict(i, &p, placed, obstacles)
                    || centre_lines(&p.lines)
                {
                    return false;
                }
            }
            let lines = [[*t, *q], [*q, *e]];
            // Ranking has already measured heads for run repairs. Reject their
            // known contacts and the immutable centre condition before querying
            // model ink; the complete original conjunction still adjudicates survivors.
            if (keep_off_centres && centre_lines(&lines))
                || head_on(*t, *q) > old_head
                || !route_safe(&lines)
            {
                return false;
            }
            // An incumbent elbow already on model ink places no restriction on
            // this candidate's elbow. Also reject excess text contacts before
            // querying the elbow, whose result cannot rescue that candidate.
            model_index.touching_at_most(grow(*lb, LABEL_PAD), old_text_model)
                && (old_elbow_model
                    || model_index.touching_at_most(grow([q[0], q[1], q[0], q[1]], LABEL_PAD), 0))
        };
        if clear_new_text {
            candidates.retain(route_allowed);
        }
        if candidates.is_empty() {
            continue;
        }
        // Fewest contacts first, then the least movement: the nearest acceptable route wins.
        if clear_new_text || repair_features {
            let mut graded: Vec<_> = candidates
                .into_iter()
                .map(|c| (job.footprints.crossing_count([c.2, c.3]), c))
                .collect();
            graded.sort_by(|(na, a), (nb, b)| {
                a.0.cmp(&b.0).then(na.cmp(nb)).then(a.1.total_cmp(&b.1))
            });
            candidates = graded.into_iter().map(|(_, c)| c).collect();
        } else if old_run || wrapped {
            let mut ranked: Vec<_> = candidates
                .into_iter()
                .map(|c| (head_on(c.2, c.3), length(&[[c.2, c.3], [c.3, c.4]]), c))
                .collect();
            ranked.sort_by(|a, b| {
                a.2.0
                    .cmp(&b.2.0)
                    .then(a.0.cmp(&b.0))
                    .then(a.1.total_cmp(&b.1))
            });
            candidates = ranked.into_iter().map(|(_, _, c)| c).collect();
        } else {
            candidates.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
        }
        let is_established = |t: P2, q: P2, e: P2| {
            established.as_ref().is_some_and(|p| {
                [t, q, e]
                    .iter()
                    .zip([p.lines[0][0], p.lines[0][1], p.lines[1][1]])
                    .all(|(a, b)| (0..2).all(|k| (a[k] - b[k]).abs() < 1e-6))
            })
        };
        if restore_established {
            // Preserve a still-clear established leader before shortening it
            // through a different physical edge after the witness reflow.
            candidates.sort_by_key(|c| !(c.0.0 == 0 && is_established(c.2, c.3, c.4)));
        }
        let mut fixed = Fixed::new(
            placed
                .iter()
                .enumerate()
                .filter(|(j, _)| *j != i)
                .filter_map(|(j, p)| Some((j, p.as_ref()?)))
                .chain(
                    obstacles
                        .iter()
                        .enumerate()
                        .filter_map(|(j, d)| Some((items.len() + j, d.placed.as_ref()?))),
                ),
        );
        fixed.furniture = obstacles
            .iter()
            .enumerate()
            .filter(|(_, d)| d.kind == "ink")
            .map(|(j, d)| (items.len() + j, d.basis == "global_axis"))
            .collect();
        fixed.crosses = obstacles
            .iter()
            .enumerate()
            .filter(|(_, d)| d.kind == "ink" && d.basis == "cross")
            .map(|(j, _)| items.len() + j)
            .collect();
        let Some(prior) = measure_at(&job, tip, elbow, end, right, &fixed, page) else {
            continue;
        };
        let interior = views
            .iter()
            .find(|v| v.id.trim_start_matches("v:") == view)
            .and_then(|v| {
                proofs
                    .get(&v.id)
                    .filter(|proof| proof.annotation_clear(v, &old, true))
                    .map(|proof| (v, proof))
            });
        let directed_contacts = |p: &Placed| {
            placed
                .iter()
                .enumerate()
                .filter(|(j, _)| *j != i)
                .filter_map(|(_, q)| q.as_ref())
                .map(|q| {
                    usize::from(
                        p.lines
                            .iter()
                            .any(|s| q.lines.iter().chain(&q.arrows).any(|t| lines_meet(*s, *t))),
                    ) + usize::from(
                        q.arrows.len() == 1
                            && q.lines.iter().any(|s| {
                                p.lines.iter().chain(&p.arrows).any(|t| lines_meet(*s, *t))
                            }),
                    )
                })
                .sum::<usize>()
        };
        let prior_contacts = directed_contacts(&old);
        let acceptable = |c: &Candidate| {
            // The initial search penalises finite extension-corridor contacts
            // in its hard tier. Here they retain their separate no-increase
            // gate above. A new callout may clear real text blockers while
            // retaining fewer such crossings; every physical hard blocker
            // (sheet, view, material re-entry, length and furniture) stays zero.
            let corridors = extension_index
                .contacts(&[[c.tip, c.elbow], [c.elbow, c.end]], c.label_box)
                .0 as u32;
            if (repair_features && c.feature_crossings >= prior.feature_crossings)
                || c.hard > prior.hard
                || (clear_new_text && c.hard > corridors)
                || c.crossings > prior.crossings
                || (repair_cross && directed_contacts(&c.placed()) >= prior_contacts)
                || (!clear_new_text && c.feature_crossings > prior.feature_crossings)
                || (!clear_new_text && c.outside > prior.outside)
                || c.text > prior.text
                || c.clearance > prior.clearance
                || (!clear_new_text && c.furniture > prior.furniture)
                || c.beside > prior.beside
                || c.grazing > prior.grazing
            {
                return None;
            }
            let p = c.placed();
            // Fixed-ink scoring does not include a peer's arrow shaft in both
            // directions. A repair must not create a new close parallel leader
            // or arrow contact while clearing its witness corridor.
            if directed_contacts(&p) > prior_contacts {
                return None;
            }
            if (clear_new_text && !page.fits(&p, view))
                || (repair_text && text_conflict(i, &p, placed, obstacles))
            {
                return None;
            }
            if interior.is_some_and(|(v, proof)| !proof.annotation_clear(v, &p, true))
                && !(new_recess && page.fits(&p, view))
            {
                return None;
            }
            Some(p)
        };
        // Measuring a station against the placed ink is the expensive tier, so it runs last.
        // Filtering after this stable sort retains the original selected route.
        let chosen = candidates
            .iter()
            .filter(|c| clear_new_text || route_allowed(c))
            .find_map(|&(_, _, t, q, e, right, lb)| {
                let mut c = measure_at_bounds(&job, t, q, e, right, page)?;
                let shift = [lb[0] - c.label_box[0], lb[1] - c.label_box[1]];
                if shift != [0.0; 2] {
                    c.label_at[0] += shift[0];
                    c.label_at[1] += shift[1];
                    c.label_box = lb;
                    c.bounds = [
                        c.bounds[0].min(lb[0]),
                        c.bounds[1].min(lb[1]),
                        c.bounds[2].max(lb[2]),
                        c.bounds[3].max(lb[3]),
                    ];
                    if !page.fits(&c.placed(), view) {
                        return None;
                    }
                }
                measure_fixed(&mut c, &job, &fixed);
                c.measure_features(&job);
                acceptable(&c)
            });
        if let Some(p) = chosen {
            updated.insert(
                w.id.clone(),
                route_reports(w, view, job.footprints.solids(), p.lines[0]),
            );
            placed[i] = Some(p);
        }
    }
    updated
}

/// A view's straight line-work and the boxes of its line-work that is not known exactly.
type ViewInk = (Vec<[P2; 2]>, Vec<[f64; 4]>);
type RefinementInk = (SegmentIndex, SegmentIndex, Vec<[f64; 4]>, Grid);

/// A view's model line-work on the page (edges and square-on cylinder silhouettes), and the
/// boxes whose ink is not known as lines (the isometric view, section views, other curved faces).
pub(super) fn view_ink(
    analysis: &crate::model::Analysis,
    ink: &crate::model::projected_ink::EdgeInk,
    v: &crate::model::PlannedView,
) -> ViewInk {
    let mut model: Vec<[P2; 2]> = Vec::new();
    let mut uncertain: Vec<[f64; 4]> = Vec::new();
    let frame = Frame::new(analysis, v);
    if v.id == "v:iso" {
        let b = analysis.iso_bounds;
        uncertain.push([
            v.at[0] + v.scale * b[0],
            v.at[1] + v.scale * b[1],
            v.at[0] + v.scale * b[2],
            v.at[1] + v.scale * b[3],
        ]);
        return (model, uncertain);
    }
    if v.cut.is_some() || v.detail.as_ref().is_some_and(|d| d.source_cut.is_some()) {
        // Section ink needs a cut B-rep: new routes stay outside its envelope.
        uncertain.push(frame.outline());
        return (model, uncertain);
    }
    model.extend(
        ink.edges
            .iter()
            .flat_map(|e| e.windows(2).map(|s| [frame.page(s[0]), frame.page(s[1])])),
    );
    for f in &ink.curved {
        let [lo, hi] = f.bounds;
        let corners: Vec<_> = (0..8)
            .map(|i| [0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] }))
            .collect();
        if let Some(c) = &f.cylinder {
            let norm = c.axis.iter().map(|x| x * x).sum::<f64>().sqrt();
            let axis = c.axis.map(|x| x / norm.max(1e-12));
            let along: f64 = (0..3).map(|k| axis[k] * v.direction[k]).sum();
            if along.abs() > 1.0 - 1e-9 {
                continue;
            }
            if along.abs() < 1e-9 {
                let h: Vec<f64> = corners
                    .iter()
                    .map(|p| (0..3).map(|k| (p[k] - c.origin[k]) * axis[k]).sum())
                    .collect();
                let h0 = h.iter().copied().fold(f64::INFINITY, f64::min);
                let h1 = h.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                let w = [
                    axis[1] * v.direction[2] - axis[2] * v.direction[1],
                    axis[2] * v.direction[0] - axis[0] * v.direction[2],
                    axis[0] * v.direction[1] - axis[1] * v.direction[0],
                ];
                for sign in [-1.0, 1.0] {
                    let at = |t| {
                        frame.page(
                            [0, 1, 2].map(|k| c.origin[k] + t * axis[k] + sign * c.radius * w[k]),
                        )
                    };
                    model.push([at(h0), at(h1)]);
                }
                continue;
            }
        }
        let b = corners.into_iter().map(|p| frame.page(p)).fold(
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
        uncertain.push(b);
    }
    (model, uncertain)
}

fn length(lines: &[[P2; 2]]) -> f64 {
    lines
        .iter()
        .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
        .sum()
}

fn boxes_meet(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3]
}

fn grow(b: [f64; 4], d: f64) -> [f64; 4] {
    [b[0] - d, b[1] - d, b[2] + d, b[3] + d]
}

/// A conservative broad phase for the final route checks. Dense models have
/// thousands of tessellated edge segments; a candidate still receives the exact
/// same predicates, but only against segments whose closed bounds can meet it.
struct PhysicalEdge {
    bounds: [f64; 4],
    chords: Vec<[P2; 2]>,
}
impl PhysicalEdge {
    fn new(chords: Vec<[P2; 2]>) -> Self {
        let bounds = chords.iter().flatten().fold(
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
        Self { bounds, chords }
    }
    fn crossed_by(&self, lines: &[[P2; 2]]) -> bool {
        lines.iter().any(|line| {
            boxes_meet(self.bounds, segment_bounds(*line))
                && self.chords.iter().any(|edge| segments_cross(*line, *edge))
        })
    }
}

struct ResolvedInk {
    visible: SegmentIndex,
    hidden: SegmentIndex,
    drawn: SegmentIndex,
}

fn resolved_ink(
    analysis: &crate::model::Analysis,
    ink: &crate::model::projected_ink::EdgeInk,
    v: &crate::model::PlannedView,
) -> Option<ResolvedInk> {
    resolved_route_ink(analysis, ink, v, false)
}

fn resolved_route_ink(
    analysis: &crate::model::Analysis,
    ink: &crate::model::projected_ink::EdgeInk,
    v: &crate::model::PlannedView,
    authored: bool,
) -> Option<ResolvedInk> {
    use crate::output::{Curve, Item};
    if (!authored && crate::nominal::authored_pmi(analysis))
        || v.id == "v:iso"
        || v.cut.is_some()
        || v.detail.is_some()
    {
        return None;
    }
    let actual = haecceity::hlr::View::new(v.direction, v.up)?;
    let source = ink.resolved.iter().find(|r| {
        r.direction
            .iter()
            .zip(actual.toward)
            .all(|(a, b)| (a - b).abs() < 1e-9)
    })?;
    let camera = haecceity::hlr::View::new(source.direction, source.up)?;
    let frame = Frame::new(analysis, v);
    let map = |p: P2| frame.page([0, 1, 2].map(|k| camera.x[k] * p[0] + camera.y[k] * p[1]));
    let origin = map([0., 0.]);
    let transform = |c: &Curve| match c {
        Curve::Line(a, b) => Curve::Line(map(*a), map(*b)),
        Curve::Conic {
            centre,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => {
            let axis = |p: P2| {
                let q = map(p);
                [0, 1].map(|k| (q[k] - origin[k]) / v.scale)
            };
            Curve::Conic {
                centre: map(*centre),
                radii: radii.map(|r| r * v.scale),
                x_axis: axis(*x_axis),
                y_axis: axis(*y_axis),
                t0: *t0,
                t1: *t1,
            }
        }
        Curve::Beziers(p) => Curve::Beziers(
            p.iter()
                .map(|p| p.iter().map(|p| map(*p)).collect())
                .collect(),
        ),
        Curve::Rational(p) => Curve::Rational(
            p.iter()
                .map(|p| {
                    p.iter()
                        .map(|p| {
                            let q = map([p[0], p[1]]);
                            [
                                q[0] + origin[0] * (p[2] - 1.),
                                q[1] + origin[1] * (p[2] - 1.),
                                p[2],
                            ]
                        })
                        .collect()
                })
                .collect(),
        ),
    };
    let visible: Vec<_> = source
        .visible
        .iter()
        .map(|c| Item::Edge(crate::linework::stable_curve(transform(c))))
        .collect();
    let hidden = if v.line_style.hidden == Some(false) {
        Vec::new()
    } else {
        crate::linework::hidden(
            &visible,
            source
                .hidden
                .iter()
                .map(|c| Item::Edge(crate::linework::stable_curve(transform(c))))
                .collect(),
        )
    };
    fn flatten(c: &Curve, a: f64, b: f64, depth: usize, into: &mut Vec<[P2; 2]>) {
        let (p, q) = (c.at(a), c.at(b));
        let flat = [0.25, 0.5, 0.75].iter().all(|t| {
            let x = c.at(a + t * (b - a));
            (x[0] - p[0] - t * (q[0] - p[0])).hypot(x[1] - p[1] - t * (q[1] - p[1])) <= 0.002
        });
        if depth >= 20 || flat {
            into.push([p, q]);
        } else {
            let m = (a + b) * 0.5;
            flatten(c, a, m, depth + 1, into);
            flatten(c, m, b, depth + 1, into);
        }
    }
    let sample = |items: &[Item]| {
        let mut lines = Vec::new();
        for item in items {
            if let Item::Edge(c) = item {
                let c = crate::output::inspection_curve(c);
                match c {
                    Curve::Line(a, b) => lines.push([a, b]),
                    Curve::Beziers(pieces) => {
                        for p in pieces {
                            if p.len() == 2 {
                                lines.push([p[0], p[1]]);
                            } else {
                                flatten(&Curve::Beziers(vec![p]), 0., 1., 0, &mut lines);
                            }
                        }
                    }
                    _ => {
                        for i in 0..4 {
                            flatten(&c, i as f64 / 4., (i + 1) as f64 / 4., 0, &mut lines);
                        }
                    }
                }
            }
        }
        lines
    };
    let visible = sample(&visible);
    let hidden = sample(&hidden);
    let drawn = SegmentIndex::new(visible.iter().chain(&hidden).copied().collect());
    Some(ResolvedInk {
        visible: SegmentIndex::new(visible),
        hidden: SegmentIndex::exported(hidden),
        drawn,
    })
}

pub(super) struct SegmentIndex {
    bounds: [f64; 4],
    ink: Vec<[P2; 2]>,
    physical: Vec<PhysicalEdge>,
    resolved: std::cell::OnceCell<Option<Box<ResolvedInk>>>,
    exported: bool,
    children: Option<Box<[SegmentIndex; 2]>>,
    counts: std::cell::RefCell<std::collections::HashMap<[u64; 4], usize>>,
    besides: std::cell::RefCell<std::collections::HashMap<[u64; 4], bool>>,
    routes: std::cell::RefCell<std::collections::HashMap<[u64; 8], usize>>,
    touching: std::cell::RefCell<std::collections::HashMap<[u64; 4], (usize, bool)>>,
}

impl SegmentIndex {
    pub(super) fn for_view(
        analysis: &crate::model::Analysis,
        ink: &crate::model::projected_ink::EdgeInk,
        view: &crate::model::PlannedView,
    ) -> (Self, Vec<[f64; 4]>) {
        let (model, uncertain) = view_ink(analysis, ink, view);
        let mut physical = Vec::new();
        if view.id != "v:iso"
            && view.cut.is_none()
            && !view.detail.as_ref().is_some_and(|d| d.source_cut.is_some())
        {
            let frame = Frame::new(analysis, view);
            let mut n = 0;
            for edge in &ink.edges {
                let chords: Vec<_> = edge
                    .windows(2)
                    .map(|p| [frame.page(p[0]), frame.page(p[1])])
                    .collect();
                n += chords.len();
                if !chords.is_empty() {
                    physical.push(PhysicalEdge::new(chords));
                }
            }
            physical.extend(model[n..].iter().map(|s| PhysicalEdge::new(vec![*s])));
        }
        let mut index = Self::new(model);
        index.physical = physical;
        (index, uncertain)
    }

    fn inspection<'a>(
        &'a self,
        analysis: &crate::model::Analysis,
        view: &crate::model::PlannedView,
    ) -> Option<&'a ResolvedInk> {
        self.resolved
            .get_or_init(|| resolved_ink(analysis, analysis.edge_ink.as_ref()?, view).map(Box::new))
            .as_deref()
    }

    pub(super) fn new(mut ink: Vec<[P2; 2]>) -> Self {
        let bounds = ink.iter().map(|s| segment_bounds(*s)).fold(
            [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ],
            |a, b| {
                [
                    a[0].min(b[0]),
                    a[1].min(b[1]),
                    a[2].max(b[2]),
                    a[3].max(b[3]),
                ]
            },
        );
        let children = if ink.len() > 16 {
            let axis = usize::from(bounds[3] - bounds[1] > bounds[2] - bounds[0]);
            ink.sort_unstable_by(|a, b| {
                (a[0][axis] + a[1][axis]).total_cmp(&(b[0][axis] + b[1][axis]))
            });
            let other = ink.split_off(ink.len() / 2);
            Some(Box::new([
                Self::new(std::mem::take(&mut ink)),
                Self::new(other),
            ]))
        } else {
            None
        };
        Self {
            bounds,
            ink,
            physical: Vec::new(),
            resolved: std::cell::OnceCell::new(),
            exported: false,
            children,
            counts: std::cell::RefCell::default(),
            besides: std::cell::RefCell::default(),
            routes: std::cell::RefCell::default(),
            touching: std::cell::RefCell::default(),
        }
    }

    fn append_ink(&self, into: &mut Vec<[P2; 2]>) {
        if let Some(children) = &self.children {
            for child in children.iter() {
                child.append_ink(into);
            }
        } else {
            into.extend(&self.ink);
        }
    }

    /// Exact closed-box stroke count, reused by labels and elbows on the same frame.
    fn touching(&self, bounds: [f64; 4]) -> usize {
        let key = bounds.map(f64::to_bits);
        if let Some(&(count, true)) = self.touching.borrow().get(&key) {
            return count;
        }
        let count = self.touching_uncached(bounds);
        let mut saved = self.touching.borrow_mut();
        if saved.len() >= 32768 {
            saved.clear();
        }
        saved.insert(key, (count, true));
        count
    }

    // Candidate text only needs to stay within the incumbent's contact count.
    // A failed bounded query records a lower bound; a complete query records
    // its exact count, so later thresholds cannot reuse an incomplete result.
    fn touching_at_most(&self, bounds: [f64; 4], maximum: usize) -> bool {
        let key = bounds.map(f64::to_bits);
        if let Some(&(count, complete)) = self.touching.borrow().get(&key)
            && (complete || count > maximum)
        {
            return count <= maximum;
        }
        let result = self.touching_bounded(bounds, maximum);
        let mut saved = self.touching.borrow_mut();
        if saved.len() >= 32768 {
            saved.clear();
        }
        saved.insert(
            key,
            (
                result.unwrap_or_else(|| maximum.saturating_add(1)),
                result.is_some(),
            ),
        );
        result.is_some()
    }

    fn touching_bounded(&self, bounds: [f64; 4], maximum: usize) -> Option<usize> {
        if !boxes_meet(self.bounds, bounds) {
            return Some(0);
        }
        let mut count = 0;
        if let Some(children) = &self.children {
            for child in children.iter() {
                count += child.touching_bounded(bounds, maximum - count)?;
            }
        } else {
            for s in &self.ink {
                if boxes_meet(segment_bounds(*s), bounds) && touches_box(*s, bounds) {
                    count += 1;
                    if count > maximum {
                        return None;
                    }
                }
            }
        }
        Some(count)
    }

    fn touching_uncached(&self, bounds: [f64; 4]) -> usize {
        if !boxes_meet(self.bounds, bounds) {
            return 0;
        }
        if let Some(children) = &self.children {
            children
                .iter()
                .map(|child| child.touching_uncached(bounds))
                .sum()
        } else {
            self.ink
                .iter()
                .filter(|s| boxes_meet(segment_bounds(**s), bounds) && touches_box(**s, bounds))
                .count()
        }
    }

    #[cfg(test)]
    fn near(&self, bounds: [f64; 4]) -> Vec<&[P2; 2]> {
        let mut found = Vec::new();
        self.collect(bounds, &mut found);
        found
    }

    pub(super) fn crossings(&self, line: [P2; 2]) -> usize {
        let key = [
            line[0][0].to_bits(),
            line[0][1].to_bits(),
            line[1][0].to_bits(),
            line[1][1].to_bits(),
        ];
        if let Some(&count) = self.counts.borrow().get(&key) {
            return count;
        }
        let count = self.crossings_uncached(line);
        let mut saved = self.counts.borrow_mut();
        if saved.len() >= 8192 {
            saved.clear();
        }
        saved.insert(key, count);
        count
    }

    fn meets_line(&self, line: [P2; 2]) -> bool {
        self.meets_padded_line(line, 1e-9)
    }

    fn meets_padded_line(&self, line: [P2; 2], pad: f64) -> bool {
        // A diagonal witness's bounding rectangle can cover most of a dense
        // view. Clip the actual shaft against each tree node instead; keep
        // closed, slightly widened bounds so even degenerate leaves survive.
        // The final segment crossing predicate is unchanged.
        let bounds = grow(self.bounds, pad);
        let mut interval = [0.0_f64, 1.0_f64];
        for k in 0..2 {
            let d = line[1][k] - line[0][k];
            if d == 0.0 {
                if line[0][k] < bounds[k] || line[0][k] > bounds[k + 2] {
                    return false;
                }
            } else {
                let a = (bounds[k] - line[0][k]) / d;
                let b = (bounds[k + 2] - line[0][k]) / d;
                interval[0] = interval[0].max(a.min(b));
                interval[1] = interval[1].min(a.max(b));
                if interval[0] > interval[1] {
                    return false;
                }
            }
        }
        true
    }

    fn crossings_uncached(&self, line: [P2; 2]) -> usize {
        if !self.meets_line(line) {
            return 0;
        }
        if let Some(children) = &self.children {
            children
                .iter()
                .map(|child| child.crossings_uncached(line))
                .sum()
        } else {
            self.ink
                .iter()
                .filter(|s| segments_cross(line, **s))
                .count()
        }
    }

    fn exported(ink: Vec<[P2; 2]>) -> Self {
        fn mark(index: &mut SegmentIndex) {
            index.exported = true;
            if let Some(children) = &mut index.children {
                for child in children.iter_mut() {
                    mark(child);
                }
            }
        }
        let mut index = Self::new(ink);
        mark(&mut index);
        index
    }

    // Count each projected edge once, even when both legs cross it. This is
    // the router's original full-scan predicate, queried through the same tree.
    fn route_crossings(&self, lines: [[P2; 2]; 2]) -> usize {
        let key = std::array::from_fn(|i| lines[i / 4][i / 2 % 2][i % 2].to_bits());
        if let Some(&n) = self.routes.borrow().get(&key) {
            return n;
        }
        let n = self.route_crossings_uncached(lines);
        let mut saved = self.routes.borrow_mut();
        if saved.len() >= 8192 {
            saved.clear();
        }
        saved.insert(key, n);
        n
    }

    fn route_crossings_uncached(&self, lines: [[P2; 2]; 2]) -> usize {
        if !lines.iter().any(|line| self.meets_line(*line)) {
            return 0;
        }
        if let Some(children) = &self.children {
            children
                .iter()
                .map(|child| child.route_crossings_uncached(lines))
                .sum()
        } else {
            self.ink
                .iter()
                .filter(|edge| {
                    lines.iter().any(|line| {
                        if self.exported {
                            exported_cross(*line, **edge)
                        } else {
                            segments_cross(*line, **edge)
                        }
                    })
                })
                .count()
        }
    }

    // A refinement must not buy a shorter route by crossing a different
    // original edge. Preserve edge identity and the exact leaf predicates.
    fn new_crossing(&self, next: &[[P2; 2]], old: &[[P2; 2]]) -> bool {
        if !next.iter().any(|line| self.meets_line(*line)) {
            return false;
        }
        if let Some(children) = &self.children {
            children.iter().any(|child| child.new_crossing(next, old))
        } else {
            self.ink.iter().any(|edge| {
                next.iter().any(|line| segments_cross(*line, *edge))
                    && !old.iter().any(|line| segments_cross(*line, *edge))
            })
        }
    }

    fn new_physical_count(&self, next: &[[P2; 2]], old: &[[P2; 2]]) -> usize {
        if self.physical.is_empty() {
            return usize::from(self.new_crossing(next, old));
        }
        self.physical
            .iter()
            .filter(|edge| edge.crossed_by(next) && !edge.crossed_by(old))
            .count()
    }

    /// Compare original CAD edges, not the sampled chords of the same curve.
    /// Different parts of one curved edge retain its physical identity.
    pub(super) fn new_physical_crossing(&self, next: &[[P2; 2]], old: &[[P2; 2]]) -> bool {
        if self.physical.is_empty() {
            return self.new_crossing(next, old);
        }
        self.physical
            .iter()
            .any(|edge| edge.crossed_by(next) && !edge.crossed_by(old))
    }

    pub(super) fn crosses_box(&self, bounds: [f64; 4]) -> bool {
        if !boxes_meet(self.bounds, bounds) {
            return false;
        }
        if let Some(children) = &self.children {
            children.iter().any(|child| child.crosses_box(bounds))
        } else {
            self.ink
                .iter()
                .any(|line| crate::dims::placement::crosses(*line, bounds))
        }
    }

    #[cfg(test)]
    fn collect<'a>(&'a self, bounds: [f64; 4], found: &mut Vec<&'a [P2; 2]>) {
        if !boxes_meet(self.bounds, bounds) {
            return;
        }
        if let Some(children) = &self.children {
            for child in children.iter() {
                child.collect(bounds, found);
            }
        } else {
            found.extend(
                self.ink
                    .iter()
                    .filter(|s| boxes_meet(segment_bounds(**s), bounds)),
            );
        }
    }

    fn collect_beside(&self, line: [P2; 2], found: &mut Vec<(f64, f64)>) {
        // Any interval within the 0.3 mm perpendicular band must meet
        // these expanded closed bounds, including a nearly diagonal run.
        if !self.meets_padded_line(line, 0.3 + 1e-9) {
            return;
        }
        if let Some(children) = &self.children {
            for child in children.iter() {
                child.collect_beside(line, found);
            }
        } else {
            found.extend(self.ink.iter().filter_map(|e| along_interval(line, *e)));
        }
    }

    fn runs_beside(&self, line: [P2; 2]) -> bool {
        let key = [
            line[0][0].to_bits(),
            line[0][1].to_bits(),
            line[1][0].to_bits(),
            line[1][1].to_bits(),
        ];
        if let Some(&result) = self.besides.borrow().get(&key) {
            return result;
        }
        // As in [`runs_beside`]: a segment of 2 mm or less cannot run beside anything.
        if (line[1][0] - line[0][0]).hypot(line[1][1] - line[0][1]) <= 2.0 + 1e-6 {
            return false;
        }
        let mut intervals = Vec::new();
        self.collect_beside(line, &mut intervals);
        let result = covered_run(line, intervals);
        let mut saved = self.besides.borrow_mut();
        if saved.len() >= 8192 {
            saved.clear();
        }
        saved.insert(key, result);
        result
    }
}

// The boolean form needs no distinct-owner bookkeeping. A positive owner
// count is exactly one of these unchanged finite-corridor predicates passing.
pub(super) fn meets_extensions(extensions: &[[P2; 2]], lines: &[[P2; 2]], label: [f64; 4]) -> bool {
    let label = grow(label, LABEL_PAD);
    extensions.iter().any(|e| {
        touches_box(*e, label)
            || lines
                .iter()
                .any(|s| touches_box(*s, grow(segment_bounds(*e), LABEL_PAD)))
    })
}

/// A dimension whose witness lines a leader must keep clear of: every linear dimension, and a
/// profile-corner angle (its witnesses are extension lines too).
fn witnessed(d: &PlannedDimension) -> bool {
    d.kind == "linear"
        || d.measurements
            .iter()
            .any(|m| m.parameter == super::profile_angles::PARAMETER)
}

/// A callout's contacts with the extension lines of linear dimensions (decision (iii)):
/// the dimensions whose extension line the leader or the padded label touches, and of those,
/// the ones the padded label touches.
pub(super) fn extension_contacts(
    extensions: &[(usize, [P2; 2])],
    lines: &[[P2; 2]],
    label: [f64; 4],
) -> (usize, usize) {
    // Invariant: The counter returns None only for a supplied bound; this call supplies none.
    extension_contacts_bounded(extensions, lines, label, None).expect("an unbounded count")
}

/// Contacts only accumulate. Stop a candidate once it cannot pass the existing gates;
/// every surviving candidate retains its exact unique-owner counts for the stable ranking.
fn extension_contacts_bounded(
    extensions: &[(usize, [P2; 2])],
    lines: &[[P2; 2]],
    label: [f64; 4],
    maximum: Option<(usize, usize)>,
) -> Option<(usize, usize)> {
    let pad = grow(label, LABEL_PAD);
    let mut pairs = std::collections::BTreeSet::new();
    let mut text = std::collections::BTreeSet::new();
    for (j, e) in extensions {
        let b = grow(
            [
                e[0][0].min(e[1][0]),
                e[0][1].min(e[1][1]),
                e[0][0].max(e[1][0]),
                e[0][1].max(e[1][1]),
            ],
            LABEL_PAD,
        );
        let on_text = touches_box(*e, pad);
        if on_text {
            text.insert(*j);
        }
        if on_text || lines.iter().any(|s| touches_box(*s, b)) {
            pairs.insert(*j);
        }
        if maximum.is_some_and(|(p, t)| pairs.len() > p || text.len() > t) {
            return None;
        }
    }
    Some((pairs.len(), text.len()))
}

/// Bounds for broad-phase lookup. Non-finite geometry must remain an always-tested item.
fn segment_bounds(s: [P2; 2]) -> [f64; 4] {
    if s.iter().flatten().any(|v| !v.is_finite()) {
        return [f64::NAN; 4];
    }
    [
        s[0][0].min(s[1][0]),
        s[0][1].min(s[1][1]),
        s[0][0].max(s[1][0]),
        s[0][1].max(s[1][1]),
    ]
}

/// The grid changes only the candidate set: preserve the exact per-stroke predicate,
/// duplicate-stroke counts, inclusive boundaries and non-finite fallback of the full scan.
#[cfg(test)]
fn model_contacts(model: &[[P2; 2]], grid: &Grid, q: P2, label: [f64; 4]) -> (usize, bool) {
    let pad = grow(label, LABEL_PAD);
    let at = grow([q[0], q[1], q[0], q[1]], LABEL_PAD);
    let mut text = 0;
    grid.near(0, pad, |i| text += usize::from(touches_box(model[i], pad)));
    let mut elbow = false;
    grid.near(0, at, |i| elbow |= !elbow && touches_box(model[i], at));
    (text, elbow)
}

/// Whether a leader segment runs within 0.3 mm of the given line-work for over 2 mm.
pub(super) fn runs_beside(s: [P2; 2], ink: &[[P2; 2]]) -> bool {
    // Every interval is a fraction of the segment, so a segment of 2 mm or less has no run.
    if (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]) <= 2.0 + 1e-6 {
        return false;
    }
    covered_run(
        s,
        ink.iter().filter_map(|e| along_interval(s, *e)).collect(),
    )
}

/// Whether the [`along_interval`]s of line-work beside *s* join into a run of over 2 mm.
fn covered_run(s: [P2; 2], mut intervals: Vec<(f64, f64)>) -> bool {
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0));
    let length = (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]);
    let (mut start, mut stop) = (0.0_f64, 0.0_f64);
    for (lo, hi) in intervals {
        if lo > stop + 1e-9 {
            start = lo;
        }
        stop = stop.max(hi);
        if (stop - start) * length > 2.0 + 1e-6 {
            return true;
        }
    }
    false
}

pub(super) fn touches_box(s: [P2; 2], b: [f64; 4]) -> bool {
    if (s[0][0] < b[0] && s[1][0] < b[0])
        || (s[0][0] > b[2] && s[1][0] > b[2])
        || (s[0][1] < b[1] && s[1][1] < b[1])
        || (s[0][1] > b[3] && s[1][1] > b[3])
    {
        return false;
    }

    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        let d = s[1][k] - s[0][k];
        if d.abs() < 1e-12 {
            if s[0][k] < b[k] || s[0][k] > b[k + 2] {
                return false;
            }
        } else {
            let a = (b[k] - s[0][k]) / d;
            let z = (b[k + 2] - s[0][k]) / d;
            lo = lo.max(a.min(z));
            hi = hi.min(a.max(z));
        }
    }
    lo <= hi
}

/// Whether the candidate introduces any path inside an uncertain material box.
/// Collinear extension in empty paper is safe only when its entire clipped
/// material segment was already covered by one established straight segment.
/// A new path can differ only inside a box it actually meets. The grid discards
/// unrelated uncertain faces; every survivor uses the original exact predicate.
fn unknown_route_safe(
    lines: &[[P2; 2]],
    boxes: &[[f64; 4]],
    grid: &Grid,
    historical: &[[P2; 2]],
    old: &[[P2; 2]],
    ordinary: bool,
) -> bool {
    let mut bounds = lines.iter().map(|s| segment_bounds(*s)).fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |a, b| {
            [
                a[0].min(b[0]),
                a[1].min(b[1]),
                a[2].max(b[2]),
                a[3].max(b[3]),
            ]
        },
    );
    if lines.iter().flatten().flatten().any(|v| !v.is_finite()) {
        bounds = [f64::NAN; 4];
    }
    grid.try_near(0, bounds, |i| {
        let b = boxes[i];
        if ordinary {
            !lines
                .iter()
                .enumerate()
                .any(|(i, s)| buried(*s, b) > buried(old[i], b) + 1e-6)
        } else {
            !lines.iter().any(|s| adds_unknown_path(*s, b, historical))
        }
    })
}

fn buried(s: [P2; 2], b: [f64; 4]) -> f64 {
    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        let d = s[1][k] - s[0][k];
        if d.abs() < 1e-12 {
            if s[0][k] < b[k] || s[0][k] > b[k + 2] {
                return 0.0;
            }
        } else {
            let p = (b[k] - s[0][k]) / d;
            let q = (b[k + 2] - s[0][k]) / d;
            lo = lo.max(p.min(q));
            hi = hi.min(p.max(q));
        }
    }
    (hi - lo).max(0.0) * (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1])
}

fn adds_unknown_path(s: [P2; 2], b: [f64; 4], old: &[[P2; 2]]) -> bool {
    if (s[0][0] < b[0] && s[1][0] < b[0])
        || (s[0][0] > b[2] && s[1][0] > b[2])
        || (s[0][1] < b[1] && s[1][1] < b[1])
        || (s[0][1] > b[3] && s[1][1] > b[3])
    {
        return false;
    }

    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        let d = s[1][k] - s[0][k];
        if d.abs() < 1e-12 {
            if s[0][k] < b[k] || s[0][k] > b[k + 2] {
                return false;
            }
        } else {
            let a = (b[k] - s[0][k]) / d;
            let z = (b[k + 2] - s[0][k]) / d;
            lo = lo.max(a.min(z));
            hi = hi.min(a.max(z));
        }
    }
    if lo > hi {
        return false;
    }
    let inside = [lo, hi].map(|t| [0, 1].map(|k| s[0][k] + t * (s[1][k] - s[0][k])));
    !old.iter().any(|[a, z]| {
        let d = [z[0] - a[0], z[1] - a[1]];
        let n = d[0] * d[0] + d[1] * d[1];
        n > 1e-18
            && inside.iter().all(|p| {
                let w = [p[0] - a[0], p[1] - a[1]];
                let cross = w[0] * d[1] - w[1] * d[0];
                let t = (w[0] * d[0] + w[1] * d[1]) / n;
                cross.abs() <= 1e-9 * n.sqrt() && (-1e-9..=1.0 + 1e-9).contains(&t)
            })
    })
}

/// The interval along a leader within 0.3 paper mm of another finite segment.
/// Clip both its overlap and its perpendicular distance, including near-parallel runs.
fn along_interval(a: [P2; 2], b: [P2; 2]) -> Option<(f64, f64)> {
    if (0..2).any(|k| {
        a[0][k].max(a[1][k]) < b[0][k].min(b[1][k]) - 0.3
            || a[0][k].min(a[1][k]) > b[0][k].max(b[1][k]) + 0.3
    }) {
        return None;
    }
    let d = [a[1][0] - a[0][0], a[1][1] - a[0][1]];
    let w = [b[1][0] - b[0][0], b[1][1] - b[0][1]];
    let n = w[0] * w[0] + w[1] * w[1];
    if n < 1e-18 {
        return None;
    }
    // A transverse 0.6 mm strip cannot hold 2 mm of travel. It must not join
    // neighbouring transverse contacts into an apparent run along a line.
    let length = d[0].hypot(d[1]);
    if (d[0] * w[1] - d[1] * w[0]).abs() > (2.0 * 0.3 / 2.0) * length * n.sqrt() {
        return None;
    }
    let q = [a[0][0] - b[0][0], a[0][1] - b[0][1]];
    let band = 0.3 * n.sqrt();
    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for (at, slope, min, max) in [
        (q[0] * w[0] + q[1] * w[1], d[0] * w[0] + d[1] * w[1], 0.0, n),
        (
            q[0] * w[1] - q[1] * w[0],
            d[0] * w[1] - d[1] * w[0],
            -band,
            band,
        ),
    ] {
        if slope.abs() < 1e-15 {
            if at < min || at > max {
                return None;
            }
        } else {
            let a = (min - at) / slope;
            let b = (max - at) / slope;
            lo = lo.max(a.min(b));
            hi = hi.min(a.max(b));
        }
    }
    (hi > lo).then_some((lo, hi))
}

/// The page direction of a turned step's silhouette, for its diameter's callout (*w* with a
/// turning axis), unless the view looks along the axis.
fn grazing(f: &Frame, w: &Want) -> Option<P2> {
    let t = w.turning?;
    let mut e = [0.0; 3];
    e[t] = 1.0;
    let (a, b) = (f.page([0.0; 3]), f.page(e));
    let d = [b[0] - a[0], b[1] - a[1]];
    let n = d[0].hypot(d[1]);
    (n > 1e-9 * f.scale()).then(|| [d[0] / n, d[1] / n])
}

/// A callout's sites on the page, at most [`MAX_SITES`]. A silhouette site (radius 0) that lands
/// on another silhouette site lies on the view's line of sight (a turned band's two sites along
/// the view direction, both on its axis): it is no silhouette in this view, and is left out.
pub(super) fn page_sites(
    f: &Frame,
    sites: &[(super::P3, f64)],
    arcs: &[crate::arc::ProfileArc],
) -> Vec<Site> {
    let all: Vec<Site> = if arcs.is_empty() {
        sites
            .iter()
            .map(|(c, r)| (f.page(*c), f.scale() * r, None))
            .collect()
    } else {
        arcs.iter()
            .map(|a| {
                let (c, r, span) = a.projected(|p| f.page(p));
                (c, r, Some(span))
            })
            .collect()
    };
    let near = |a: P2, b: P2| (a[0] - b[0]).abs() < 1e-6 && (a[1] - b[1]).abs() < 1e-6;
    let kept: Vec<Site> = all
        .iter()
        .enumerate()
        .filter(|(i, (p, r, _))| {
            *r > 0.0
                || !all
                    .iter()
                    .enumerate()
                    .any(|(j, (q, s, _))| j != *i && *s == 0.0 && near(*p, *q))
        })
        .map(|(_, s)| *s)
        .take(MAX_SITES)
        .collect();
    // A concave short arc may need a leader approaching across its opening.
    // The nearest radial point can be a crowded tangent endpoint. Reuse the
    // proven-point attachment contract of blends for interior arc stations too.
    let mut kept = kept;
    for arc in arcs
        .iter()
        .filter(|a| sites.len() == 1 && a.sweep < std::f64::consts::PI - 1e-6)
    {
        for fraction in [0.25, 0.5, 0.75] {
            let point = f.page(arc.point(arc.start + fraction * arc.sweep));
            if !kept.iter().any(|s| s.1 == 0.0 && near(s.0, point)) {
                kept.push((point, 0.0, None));
            }
        }
    }
    // A site set wholly on the line of sight still names the feature: keep it rather than none.
    if kept.is_empty() {
        all.into_iter().take(MAX_SITES).collect()
    } else {
        kept
    }
}

/// The tip on *site*'s rim radial towards *elbow* (draftwright's `_rim_tip`).
fn rim_tip(site: Site, elbow: P2) -> P2 {
    let (c, r, span) = site;
    let d = [elbow[0] - c[0], elbow[1] - c[1]];
    let n = d[0].hypot(d[1]);
    if span.is_none() && (n <= r || n < 1e-12) {
        return c;
    }
    if let Some(span) = span {
        let a = if n < 1e-12 {
            span.start + span.sweep / 2.0
        } else {
            span.clamp(d[1].atan2(d[0]))
        };
        return [c[0] + r * a.cos(), c[1] + r * a.sin()];
    }
    [c[0] + r * d[0] / n, c[1] + r * d[1] / n]
}

/// A transported compact route can end on the model grid rather than the
/// native circular rim. Remove only that rounding before candidate audits.
/// There is no route search, label movement or change to authored placements.
pub(crate) fn restore_proven_arc_tips(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    plan: &mut crate::model::Plan,
) {
    if crate::nominal::comparison() {
        return;
    }
    let ids: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.placed.is_some()
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "slot_end_radius.radius")
                && spec.dimensions.get(&d.id).is_none_or(|d| d.place.is_none())
        })
        .map(|d| (d.id.clone(), d.view.clone()))
        .collect();
    if ids.is_empty() {
        return;
    }
    // The live native evidence, also used by callout_offset, is independent
    // of the compact comparison's temporary placement policies.
    let corrections = crate::planning::isolated(analysis, || {
        ids.iter()
            .filter_map(|(id, view)| {
                let dimension = plan.dimensions.iter().find(|d| d.id == *id)?;
                let old = dimension.placed.as_ref()?.lines.first()?[0];
                let scale = plan.views.iter().find(|v| v.id == *view)?.scale;
                let sites: Vec<Site> = super::callout_members(analysis, spec, plan, id, view)
                    .into_iter()
                    .map(|m| {
                        (
                            m.centre,
                            m.radius,
                            m.span.map(|s| crate::arc::Span {
                                start: s.start,
                                sweep: s.sweep,
                            }),
                        )
                    })
                    .collect();
                let tip = rounded_rim_tip(&sites, old, scale * crate::model::projected_ink::GRID);
                (tip != old).then(|| (id.clone(), tip))
            })
            .collect::<Vec<_>>()
    });
    if corrections.is_empty() {
        return;
    }
    let corrected: std::collections::BTreeSet<_> =
        corrections.iter().map(|(id, _)| id.clone()).collect();
    let affected = |p: &Problem| p.subjects.iter().any(|id| corrected.contains(id));
    let before: Vec<_> = super::recheck(analysis, plan)
        .into_iter()
        .filter(&affected)
        .collect();
    for (id, tip) in corrections {
        let d = plan
            .dimensions
            .iter_mut()
            .find(|d| d.id == id)
            .expect("corrected dimension");
        let p = d.placed.as_mut().expect("corrected placement");
        p.lines[0][0] = tip;
        p.arrows[0] = arrow(tip, p.lines[0][1]);
        let at = plan
            .views
            .iter()
            .find(|v| v.id == d.view)
            .expect("corrected carrier")
            .at;
        plan.callout_tips
            .insert(id, (d.view.clone(), [tip[0] - at[0], tip[1] - at[1]]));
    }
    let after: Vec<_> = super::recheck(analysis, plan)
        .into_iter()
        .filter(&affected)
        .collect();
    let codes: std::collections::BTreeSet<_> = before
        .into_iter()
        .chain(after.iter().cloned())
        .map(|p| p.code)
        .collect();
    plan.problems
        .retain(|p| !(codes.contains(&p.code) && affected(p)));
    plan.problems.extend(after);
    plan.sheet.fill = crate::sheet_fit::fill(analysis, plan);
    plan.status = crate::completeness::status(&plan.problems);
}

/// Remove model-grid rounding from a copied arc attachment without changing
/// its angular station. This is geometry transport, not a placement search.
fn rounded_rim_tip(sites: &[Site], tip: P2, grid: f64) -> P2 {
    let error = |s: &Site| ((tip[0] - s.0[0]).hypot(tip[1] - s.0[1]) - s.1).abs();
    if let Some(site) = sites
        .iter()
        .filter(|s| {
            s.1 > 0.
                && s.2.is_some_and(|span| {
                    span.contains((tip[1] - s.0[1]).atan2(tip[0] - s.0[0]), grid / s.1)
                })
        })
        .min_by(|a, b| error(a).total_cmp(&error(b)))
        && error(site) > 1e-6
        && error(site) <= grid
    {
        rim_tip(*site, tip)
    } else {
        tip
    }
}

/// Reattach to the same proven rim when a bounded growth repair moves its elbow.
fn moved_rim_tip(sites: &[Site], anchor: P2, elbow: P2, grid: f64) -> P2 {
    let on = |s: &Site| ((s.0[0] - anchor[0]).hypot(s.0[1] - anchor[1]) - s.1).abs();
    // Arc sampling also contributes zero-radius surface points. Prefer the
    // actual circle at that point so moving the elbow keeps a radial shaft.
    // Stored comparison tips use the model's grid; allow that rounding when
    // identifying their rim, then compute the new attachment on the true arc.
    let site = *sites
        .iter()
        .filter(|s| s.1 > 0.0 && on(s) <= grid.max(1e-6))
        .min_by(|a, b| on(a).total_cmp(&on(b)))
        .or_else(|| sites.iter().min_by(|a, b| on(a).total_cmp(&on(b))))
        // Invariant: Callout wants are constructed only with nonempty proven-site inventories.
        .expect("a callout has a site");
    // A retained profile attachment can be a proven surface point rather
    // than one of this group's representative sites. Do not jump to another
    // occurrence when only its elbow is being repaired.
    if on(&site) > grid.max(1e-6) {
        return anchor;
    }
    rim_tip(site, elbow)
}

/// An explicit shoulder keeps its station; attach radially to its nearest proven feature site.
pub(super) fn intent_tip(
    frame: &Frame,
    sites: &[(super::P3, f64)],
    arcs: &[crate::arc::ProfileArc],
    elbow: P2,
) -> P2 {
    rim_tip(nearest(&page_sites(frame, sites, arcs), elbow), elbow)
}

/// A pinned callout whose bend is `offset` (paper mm) from its automatic leader tip, `held`'s
/// first point (#12). The bend may sit inside the view. The leader still attaches radially to the
/// member nearest its bend, keeping the automatic member on ties. The label keeps the side
/// it would have had on the automatic member, so changing members moves only the arrow.
/// At the automatic bend it is the automatic ink itself, whatever that leader's direction.
/// Without an automatic placement in this view the offset is
/// taken from the first site's centre and the leader goes to the nearest site. The pin is never
/// slid: crossings and contacts are reported against it.
pub(super) fn offset_intent(
    frame: &Frame,
    w: &Want,
    offset: P2,
    held: Option<(&Placed, &[String])>,
) -> Option<Placed> {
    let Shape::Callout { sites, arcs } = &w.shape else {
        return None;
    };
    let sites = page_sites(frame, sites, arcs);
    let held = held.and_then(|(p, terms)| Some((p, *p.lines.first()?, terms)));
    let anchor = match held {
        Some((_, [tip, _], _)) => tip,
        None => sites.first()?.0,
    };
    let elbow = [anchor[0] + offset[0], anchor[1] + offset[1]];
    let near = |a: P2, b: P2| (a[0] - b[0]).abs() < 1e-6 && (a[1] - b[1]).abs() < 1e-6;
    if let Some((p, [_, e], terms)) = held
        && near(e, elbow)
    {
        return Some(super::placement::relabel(p, terms, w));
    }
    let (tip, label_tip) = match held {
        Some((_, [tip, _], _)) => {
            let on = |s: &Site| ((s.0[0] - tip[0]).hypot(s.0[1] - tip[1]) - s.1).abs();
            let current = *sites.iter().min_by(|a, b| on(a).total_cmp(&on(b)))?;
            // nearest keeps the first site on ties: retain the automatic member unless
            // another is strictly nearer, with the same rim/arc distance as free leaders.
            let site = nearest(&[current, nearest(&sites, elbow)], elbow);
            (rim_tip(site, elbow), rim_tip(current, elbow))
        }
        None => {
            let tip = rim_tip(nearest(&sites, elbow), elbow);
            (tip, tip)
        }
    };
    let right = if (elbow[0] - label_tip[0]).abs() > 1e-9 {
        elbow[0] > label_tip[0]
    } else {
        // A vertical leader keeps the automatic label's side, else reads to the right.
        held.is_none_or(|(p, _, _)| p.lines.get(1).is_none_or(|s| s[1][0] >= s[0][0]))
    };
    let end = [
        elbow[0] + if right { SHOULDER } else { -SHOULDER },
        elbow[1],
    ];
    let ink = label_ink(&w.terms, font());
    let (label_at, label_box) = label_geometry(elbow, end, right, ink.min, ink.max);
    Some(Placed {
        lines: vec![[tip, elbow], [elbow, end]],
        arrows: vec![arrow(tip, elbow)],
        label_at,
        rotation: 0.0,
        label_box,
    })
}

/// Keep a prescribed strip and lane. Prefer its station; shift only to reduce feature
/// crossings without increasing contacts with other prescribed pins. A pin keeps its ink;
/// the finished-plan audit reports its feature crossings. Free ink yields later.
pub(super) fn route_intent(
    footprints: &Footprints,
    frame: &Frame,
    w: &Want,
    p: &mut Placed,
    side: crate::placement_intent::Side,
    obstacles: &[(&str, &Placed)],
    pinned: bool,
) {
    if pinned {
        return;
    }
    let Shape::Callout { sites, arcs } = &w.shape else {
        return;
    };
    let Some(&[tip, elbow]) = p.lines.first() else {
        return;
    };
    let before = footprints.crossed([tip, elbow], &w.measurements).len();
    if before == 0 {
        return;
    }
    let k = usize::from(matches!(
        side,
        crate::placement_intent::Side::Left | crate::placement_intent::Side::Right
    ));
    let o = frame.outline();
    let sites = page_sites(frame, sites, arcs);
    let contacts = |tip: P2, next: P2| {
        let shift = [next[0] - elbow[0], next[1] - elbow[1]];
        let label = [
            p.label_box[0] + shift[0],
            p.label_box[1] + shift[1],
            p.label_box[2] + shift[0],
            p.label_box[3] + shift[1],
        ];
        let lines = [
            [tip, next],
            [
                next,
                [p.lines[1][1][0] + shift[0], p.lines[1][1][1] + shift[1]],
            ],
        ];
        obstacles
            .iter()
            .filter(|(id, _)| *id != w.id)
            .map(|(_, p)| *p)
            .map(|q| {
                usize::from(
                    boxes_overlap(label, q.label_box)
                        || lines.iter().any(|line| crosses(*line, q.label_box))
                        || q.lines
                            .iter()
                            .chain(&q.arrows)
                            .any(|line| crosses(*line, label)),
                ) + usize::from(lines.iter().any(|line| {
                    q.lines
                        .iter()
                        .chain(&q.arrows)
                        .any(|other| lines_meet(*line, *other))
                }))
            })
            .sum::<usize>()
    };
    let old_contacts = contacts(tip, elbow);
    let mut best = (before, 0.0, tip, elbow);
    for station in stations(elbow[k], o[k].min(elbow[k]), o[k + 2].max(elbow[k])) {
        let mut next = elbow;
        next[k] = station;
        for &site in &sites {
            let tip = rim_tip(site, next);
            let n = footprints.crossed([tip, next], &w.measurements).len();
            let shift = (station - elbow[k]).abs();
            if contacts(tip, next) > old_contacts {
                continue;
            }
            if n < best.0
                || (n == best.0 && (shift < best.1 || (shift == best.1 && station < best.3[k])))
            {
                best = (n, shift, tip, next);
            }
        }
    }
    if best.0 >= before {
        return;
    }
    let delta = [best.3[0] - elbow[0], best.3[1] - elbow[1]];
    for line in p.lines.iter_mut().skip(1) {
        for point in line {
            for k in 0..2 {
                point[k] += delta[k];
            }
        }
    }
    p.lines[0] = [best.2, best.3];
    p.arrows = vec![arrow(best.2, best.3)];
    for (k, shift) in delta.iter().enumerate() {
        p.label_at[k] += shift;
        p.label_box[k] += shift;
        p.label_box[k + 2] += shift;
    }
}

/// The candidate stations along one side: the natural one (clamped into `[lo, hi]`) first, then
/// alternately past and before it, [`STEP`] apart, at most [`MAX_STATIONS`].
fn stations(natural: f64, lo: f64, hi: f64) -> Vec<f64> {
    if lo > hi {
        return Vec::new();
    }
    let start = natural.clamp(lo, hi);
    let mut out = vec![start];
    let mut k = 1;
    while out.len() < MAX_STATIONS {
        let (up, down) = (start + k as f64 * STEP, start - k as f64 * STEP);
        if up > hi && down < lo {
            break;
        }
        if up <= hi {
            out.push(up);
        }
        if down >= lo && out.len() < MAX_STATIONS {
            out.push(down);
        }
        k += 1;
    }
    out
}

/// The distance from *p* along unit *u* to where the ray leaves box *b* (0 when *p* is outside).
fn exit_distance(p: P2, u: P2, b: [f64; 4]) -> f64 {
    if p[0] < b[0] || p[0] > b[2] || p[1] < b[1] || p[1] > b[3] {
        return 0.0;
    }
    let mut d = f64::INFINITY;
    if u[0] > 1e-12 {
        d = d.min((b[2] - p[0]) / u[0]);
    } else if u[0] < -1e-12 {
        d = d.min((b[0] - p[0]) / u[0]);
    }
    if u[1] > 1e-12 {
        d = d.min((b[3] - p[1]) / u[1]);
    } else if u[1] < -1e-12 {
        d = d.min((b[1] - p[1]) / u[1]);
    }
    if d.is_finite() { d.max(0.0) } else { 0.0 }
}

/// The site of *sites* nearest *elbow*.
fn nearest(sites: &[Site], elbow: P2) -> Site {
    sites
        .iter()
        .map(|s| {
            let distance = if s.2.is_some() {
                let tip = rim_tip(*s, elbow);
                (tip[0] - elbow[0]).hypot(tip[1] - elbow[1])
            } else {
                (s.0[0] - elbow[0]).hypot(s.0[1] - elbow[1]) - s.1
            };
            (s, distance)
        })
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .map(|(s, _)| *s)
        // Invariant: Callout wants are constructed only with nonempty proven-site inventories.
        .expect("a callout has a site")
}

/// The best [`KEEP`] candidates of *job* as if all were fully measured (fewest hard blockers,
/// then crossings, length and generation order), near-duplicates dropped.
type Inventories = (Vec<Candidate>, Vec<Candidate>, Vec<Candidate>);

fn inventory(
    job: &Job,
    fixed: &Fixed,
    page: &Page,
    semantic_pmi: bool,
    short_shoulders: bool,
) -> Inventories {
    let o = job.outline;
    let d = page.drawable();
    let mut raw: Vec<(P2, P2, Option<bool>)> = Vec::new(); // (tip, elbow, label right?)
    // Columns beside the view: the site nearest that edge, rows within the view's height.
    for right in [true, false] {
        let site = *job
            .sites
            .iter()
            .max_by(|a, b| {
                let k = |s: &Site| if right { s.0[0] } else { -s.0[0] };
                k(a).total_cmp(&k(b))
            })
            // Invariant: Callout wants are constructed only with nonempty proven-site inventories.
            .expect("a callout has a site");
        let k = if right { 3 } else { 2 };
        for off in offsets(job.beyond[k]) {
            let x = if right { o[2] + off } else { o[0] - off };
            for y in stations(site.0[1], o[1].max(d[1]), o[3].min(d[3])) {
                let elbow = [x, y];
                raw.push((
                    rim_tip(nearest(&job.sites, elbow), elbow),
                    elbow,
                    Some(right),
                ));
            }
        }
    }
    // Rows above and below: the site nearest that edge, columns within the view's width.
    for up in [false, true] {
        let site = *job
            .sites
            .iter()
            .max_by(|a, b| {
                let k = |s: &Site| if up { s.0[1] } else { -s.0[1] };
                k(a).total_cmp(&k(b))
            })
            // Invariant: Callout wants are constructed only with nonempty proven-site inventories.
            .expect("a callout has a site");
        let k = usize::from(up);
        for off in offsets(job.beyond[k]) {
            let y = if up { o[3] + off } else { o[1] - off };
            for x in stations(site.0[0], o[0].max(d[0]), o[2].min(d[2])) {
                let elbow = [x, y];
                let tip = rim_tip(nearest(&job.sites, elbow), elbow);
                raw.push((tip, elbow, None));
            }
        }
    }
    // Escapes from each site.
    for &site in &job.sites {
        for j in 0..DIRECTIONS {
            let a = std::f64::consts::TAU * j as f64 / DIRECTIONS as f64;
            let u = [a.cos(), a.sin()];
            let exit = exit_distance(site.0, u, o);
            for off in ESCAPES {
                let dist = site.1.max(exit) + off;
                let elbow = [site.0[0] + u[0] * dist, site.0[1] + u[1] * dist];
                raw.push((rim_tip(site, elbow), elbow, None));
            }
        }
    }
    if job.short_profile {
        // A hidden end rim may be represented from its visible profile. Give
        // its real end stations the same short bounded rays as interior jobs,
        // instead of forcing a long column outside the entire view envelope.
        for &site in &job.sites {
            for j in 0..DIRECTIONS {
                let a = std::f64::consts::TAU * j as f64 / DIRECTIONS as f64;
                let u = [a.cos(), a.sin()];
                let tip = rim_tip(
                    site,
                    [
                        site.0[0] + (site.1 + 1.0) * u[0],
                        site.0[1] + (site.1 + 1.0) * u[1],
                    ],
                );
                for &reach in SWEEP_REACH.iter().take(3) {
                    raw.push((tip, [tip[0] + reach * u[0], tip[1] + reach * u[1]], None));
                }
            }
        }
    }
    if let Some(side) = job.side {
        use crate::placement_intent::Side;
        raw.retain(|(_, elbow, _)| match side {
            Side::Left => elbow[0] < o[0],
            Side::Right => elbow[0] > o[2],
            Side::Above => elbow[1] > o[3],
            Side::Below => elbow[1] < o[1],
        });
    }
    let mut measured: Vec<Candidate> = Vec::new();
    for (tip, elbow, side) in raw {
        // The label reads on the side the leader runs to; a vertical leader may take either.
        let dx = elbow[0] - tip[0];
        let options: &[bool] = match side {
            Some(r) if (r && dx < -1e-9) || (!r && dx > 1e-9) => &[],
            Some(true) => &[true],
            Some(false) => &[false],
            None if dx > 1e-9 => &[true],
            None if dx < -1e-9 => &[false],
            None => &[true, false],
        };
        for &right in options {
            if let Some(c) = measure_bounds(job, tip, elbow, right, page) {
                measured.push(c);
            }
            // Qualified polygon labels may need a shorter shelf between a new
            // notch station and an existing pitch witness. Keep every ink gate.
            if short_shoulders {
                for shoulder in SHOULDER_STEPS.map(|f| f * SHOULDER) {
                    let end = [
                        elbow[0] + if right { shoulder } else { -shoulder },
                        elbow[1],
                    ];
                    if let Some(c) = measure_at_bounds(job, tip, elbow, end, right, page) {
                        measured.push(c);
                    }
                }
            }
        }
    }
    // A half-millimetre text contact need not move a short leader to another
    // side of the part. Try four shoulder-only extensions of the shortest
    // geometric route on each text side. At most eight candidates are added;
    // attachment, shaft, search-node budget and every normal ink gate stay fixed.
    if job.footprints.automatic_slot_layout() {
        let shoulders: Vec<_> = [false, true]
            .into_iter()
            .filter_map(|right| {
                measured
                    .iter()
                    .filter(|c| (c.end[0] >= c.elbow[0]) == right && c.hard == 0)
                    .min_by_key(|c| c.score())
                    .cloned()
            })
            .collect();
        for mut old in shoulders {
            measure_fixed(&mut old, job, fixed);
            old.measure_features(job);
            if old.clearance == 0 || old.crossings > 0 || old.text > 0 || old.feature_crossings > 0
            {
                continue;
            }
            for extra in [0.5, 1., 1.5, 2.] {
                let end = [
                    old.end[0]
                        + if old.end[0] >= old.elbow[0] {
                            extra
                        } else {
                            -extra
                        },
                    old.end[1],
                ];
                let Some(c) = measure_at(
                    job,
                    old.tip,
                    old.elbow,
                    end,
                    old.end[0] >= old.elbow[0],
                    fixed,
                    page,
                ) else {
                    continue;
                };
                if c.hard <= old.hard
                    && c.crossings <= old.crossings
                    && c.text <= old.text
                    && c.feature_crossings <= old.feature_crossings
                    && c.beside <= old.beside
                    && c.clearance < old.clearance
                {
                    measured.push(c);
                }
            }
        }
    }
    let mut lists = kept(&mut measured, job, fixed, semantic_pmi);
    if job.corridors
        && lists.0.first().is_none_or(|c| {
            c.hard > c.furniture_hard || c.feature_crossings > 0 || c.crossings > c.furniture_text
        })
    {
        measured.extend(
            sweep(job, job.sites[0].0)
                .into_iter()
                .filter_map(|(tip, elbow, right)| measure_bounds(job, tip, elbow, right, page)),
        );
        lists = kept(&mut measured, job, fixed, semantic_pmi);
    }
    // A turned step's diameter whose best route grazes its silhouette takes an angled route
    // instead, but only one that crosses no more of the part's line-work, and passes through
    // no more features, than the grazing one; with none, the grazing route stands.
    if job.model.is_none() {
        return lists;
    }
    let grazing: Vec<&Candidate> = [&lists.0, &lists.1, &lists.2]
        .iter()
        .filter_map(|l| l.first().filter(|c| c.grazing > 0))
        .collect();
    let (Some(edges), Some(features)) = (
        grazing.iter().map(|c| model_crossings(job, c)).min(),
        grazing.iter().map(|c| feature_count(job, c)).min(),
    ) else {
        return lists;
    };
    job.steer.set(Some((edges, features)));
    for c in &mut measured {
        c.astray = astray(job, c);
    }
    if measured.iter().all(|c| c.astray > 0) {
        job.steer.set(None);
        for c in &mut measured {
            c.astray = 0;
        }
        return lists;
    }
    kept(&mut measured, job, fixed, semantic_pmi)
}

/// How many of its view's model lines a route crosses ([`Job::model`]).
fn model_crossings(job: &Job, c: &Candidate) -> usize {
    job.model
        .map_or(0, |model| model.route_crossings(c.segments()))
}

/// How many features a route's shaft passes through.
fn feature_count(job: &Job, c: &Candidate) -> usize {
    job.footprints.crossing_count([c.tip, c.elbow])
}

/// [`Candidate::astray`] for a route of *job*, as steered ([`Job::steer`]).
fn astray(job: &Job, c: &Candidate) -> u32 {
    job.steer.get().map_or(0, |(edges, features)| {
        u32::from(
            c.grazing > 0 || model_crossings(job, c) > edges || feature_count(job, c) > features,
        )
    })
}

/// The furniture-aware, baseline-aware and legacy inventories kept from *measured*.
fn kept(measured: &mut [Candidate], job: &Job, fixed: &Fixed, semantic_pmi: bool) -> Inventories {
    let aware = keep(measured, job, fixed);
    let mut baseline = measured.to_vec();
    for c in &mut baseline {
        c.without_furniture();
        c.ignore_furniture = true;
    }
    let baseline_aware = if semantic_pmi {
        Vec::new()
    } else {
        keep(&mut baseline, job, fixed)
    };
    for c in &mut baseline {
        c.aware = false;
    }
    (aware, baseline_aware, keep(&mut baseline, job, fixed))
}

fn keep(measured: &mut [Candidate], job: &Job, fixed: &Fixed) -> Vec<Candidate> {
    // Merge sorted geometric lower bounds with refined scores. Fixed ink and feature
    // crossings only add to them; generation order still breaks every tie.
    // A fully measured inventory takes the original fast sorted-prefix path.
    let mut order: Vec<_> = measured
        .iter()
        .enumerate()
        .map(|(n, c)| (c.score(), n))
        .collect();
    order.sort_unstable();
    let mut pending: BinaryHeap<Reverse<(i64, usize)>> = BinaryHeap::new();
    let mut next = 0;
    let mut kept: Vec<Candidate> = Vec::new();
    while kept.len() < KEEP {
        let (bound, n) = if next < order.len() && pending.peek().is_none_or(|p| order[next] <= p.0)
        {
            let key = order[next];
            next += 1;
            key
        } else if let Some(Reverse(key)) = pending.pop() {
            key
        } else {
            break;
        };
        let c = &mut measured[n];
        if !c.fixed_measured {
            measure_fixed(c, job, fixed);
            if c.score() != bound {
                pending.push(Reverse((c.score(), n)));
                continue;
            }
        }
        if c.aware && !c.features_measured {
            c.measure_features(job);
            if c.score() != bound {
                pending.push(Reverse((c.score(), n)));
                continue;
            }
        }
        let same = kept.iter().any(|k| {
            (0..4).all(|j| (k.label_box[j] - c.label_box[j]).abs() < 0.25)
                && (k.tip[0] - c.tip[0]).abs() < 0.25
                && (k.tip[1] - c.tip[1]).abs() < 0.25
        });
        if !same {
            // The legacy objective ignores features, but its survivors still supply the
            // crossing ceiling for the furniture-aware solve.
            c.measure_features(job);
            kept.push(c.clone());
        }
    }
    kept
}

/// The elbow's distances past an edge: just past it and two text rows further (draftwright's
/// lane expansion, `lane_candidates`, a text height and two pads apart), and the same past the
/// strip's dimensions when they reach further.
fn offsets(beyond: f64) -> Vec<f64> {
    let mut out: Vec<f64> = (0..3).map(|k| CLEAR + k as f64 * ROW).collect();
    if beyond > CLEAR + 1e-9 {
        out.extend((0..3).map(|k| beyond + k as f64 * ROW));
    }
    out
}

/// The candidate with its tip, elbow and label side, measured; none when the leader is too short
/// for its arrowhead.
fn measure(
    job: &Job,
    tip: P2,
    elbow: P2,
    right: bool,
    fixed: &Fixed,
    page: &Page,
) -> Option<Candidate> {
    let mut c = measure_bounds(job, tip, elbow, right, page)?;
    measure_fixed(&mut c, job, fixed);
    c.measure_features(job);
    Some(c)
}

fn measure_bounds(job: &Job, tip: P2, elbow: P2, right: bool, page: &Page) -> Option<Candidate> {
    let end = [
        elbow[0] + if right { SHOULDER } else { -SHOULDER },
        elbow[1],
    ];
    measure_at_bounds(job, tip, elbow, end, right, page)
}

fn label_geometry(_elbow: P2, end: P2, right: bool, ink_min: P2, ink_max: P2) -> (P2, [f64; 4]) {
    let at_x = if right {
        end[0] + 1.0 - ink_min[0]
    } else {
        end[0] - 1.0 - ink_max[0]
    };
    let centre_y = if ink_min[1] < -CAP {
        // The shoulder meets the centre of a multiline block. Keeping the old
        // first-line anchor would put every continuation into the corridor below.
        0.5 * (ink_min[1] + ink_max[1])
    } else {
        0.5 * CAP
    };
    let label_at = [at_x, end[1] - centre_y];
    let ink = super::LabelInk {
        glyphs: Vec::new(),
        strokes: Vec::new(),
        min: ink_min,
        max: ink_max,
    };
    let lb = label_box(&ink, label_at, 0.0);
    (label_at, lb)
}

fn measure_at(
    job: &Job,
    tip: P2,
    elbow: P2,
    end: P2,
    right: bool,
    fixed: &Fixed,
    page: &Page,
) -> Option<Candidate> {
    let mut c = measure_at_bounds(job, tip, elbow, end, right, page)?;
    measure_fixed(&mut c, job, fixed);
    c.measure_features(job);
    Some(c)
}

fn radial_shaft(sites: &[Site], tip: P2, elbow: P2) -> bool {
    sites.iter().any(|&(centre, r, _)| {
        let u = [tip[0] - centre[0], tip[1] - centre[1]];
        let v = [elbow[0] - tip[0], elbow[1] - tip[1]];
        let norm = u[0].hypot(u[1]) * v[0].hypot(v[1]);
        r > 0.0
            && (u[0].hypot(u[1]) - r).abs() < crate::lint::leaders::TOLERANCE
            && (u[0] * v[1] - u[1] * v[0]).abs() <= 1e-9 * norm
            && u[0] * v[0] + u[1] * v[1] > 0.0
    })
}

fn measure_at_bounds(
    job: &Job,
    tip: P2,
    elbow: P2,
    end: P2,
    right: bool,
    page: &Page,
) -> Option<Candidate> {
    if job.radial && !radial_shaft(&job.sites, tip, elbow) {
        return None;
    }
    if job.sites.iter().any(|s| s.2.is_some())
        && !job.sites.iter().any(|&(c, r, span)| {
            span.is_some_and(|span| {
                ((tip[0] - c[0]).hypot(tip[1] - c[1]) - r).abs() <= crate::lint::leaders::TOLERANCE
                    && span.contains(
                        (tip[1] - c[1]).atan2(tip[0] - c[0]),
                        crate::lint::leaders::TOLERANCE / r.max(1e-9),
                    )
            })
        })
    {
        return None;
    }
    if (end[0] - elbow[0]) * if right { 1.0 } else { -1.0 } <= 1e-9 {
        return None;
    }
    let shaft = (elbow[0] - tip[0]).hypot(elbow[1] - tip[1]);
    if shaft < MIN_LEADER {
        return None;
    }
    let (label_at, lb) = label_geometry(elbow, end, right, job.ink_min, job.ink_max);
    let segs = [[tip, elbow], [elbow, end]];
    let mut hard = 0u32;
    // On the drawing area.
    let dr = page.drawable();
    let inside = |p: P2| {
        p[0] >= dr[0] - 1e-9 && p[0] <= dr[2] + 1e-9 && p[1] >= dr[1] - 1e-9 && p[1] <= dr[3] + 1e-9
    };
    if ![tip, elbow, end, [lb[0], lb[1]], [lb[2], lb[3]]]
        .into_iter()
        .all(inside)
    {
        hard += 1;
    }
    let tb = page.title_block();
    if boxes_overlap(lb, tb) || segs.iter().any(|s| crosses(*s, tb)) {
        hard += 1;
    }
    for (id, b) in page.views() {
        let own = id.strip_prefix("v:") == Some(job.view);
        if boxes_overlap(lb, *b) || crosses(segs[1], *b) {
            let interior = own
                && job.interior_proof.is_some_and(|(v, proof)| {
                    proof.leader_clear(
                        v,
                        &Placed {
                            lines: segs.to_vec(),
                            arrows: vec![arrow(tip, elbow)],
                            label_at,
                            rotation: 0.0,
                            label_box: lb,
                        },
                        job.edge_attachment,
                    )
                });
            if !interior {
                hard += 1;
            }
        }
        if !own && crosses(segs[0], *b) {
            hard += 1;
        }
    }
    // Material re-entry (ADR2-8): measured by the predicate lint's `leader_crosses_silhouette`
    // shares, it is a hard blocker, and its depth (in micrometres) is added to the length so a
    // shallower cut is preferred when every candidate re-enters.
    let cut = crate::material::reentry(job.footprints.solids(), segs[0]);
    if cut > 0.0 {
        hard += 1;
    }
    if too_long(shaft) {
        hard += 1;
    }
    // A leader along the silhouette it points at lies on the part's edge: ISO 128-22's leader
    // meets the outline at an angle.
    let grazing = job.silhouette.is_some_and(|u| {
        let d = [elbow[0] - tip[0], elbow[1] - tip[1]];
        (u[0] * d[1] - u[1] * d[0]).abs() < GRAZING.to_radians().sin() * shaft
    });
    let bounds = [
        tip[0].min(elbow[0]).min(end[0]).min(lb[0]),
        tip[1].min(elbow[1]).min(lb[1]),
        tip[0].max(elbow[0]).max(end[0]).max(lb[2]),
        tip[1].max(elbow[1]).max(lb[3]),
    ];
    // The ink's extreme points (its arrowhead within a hair of its tip) against the view's area.
    let outside = job.area.is_some_and(|a| {
        [tip, elbow, end, [lb[0], lb[1]], [lb[2], lb[3]]]
            .iter()
            .any(|p| {
                p[0] < a[0] - 1e-6 || p[0] > a[2] + 1e-6 || p[1] < a[1] - 1e-6 || p[1] > a[3] + 1e-6
            })
    });
    let cost =
        ((shaft + (end[0] - elbow[0]).hypot(end[1] - elbow[1]) + cut) * 1000.0).round() as i64;
    // The material's boxes are cheap; resolving this length now keeps the score bound tight.
    let inside = (job.footprints.inside_length(segs[0]) * 1000.0).round() as i64;
    let mut c = Candidate {
        tip,
        elbow,
        end,
        label_at,
        label_box: lb,
        cost,
        hard,
        crossings: 0,
        furniture: 0,
        furniture_hard: 0,
        furniture_text: 0,
        clearance: 0,
        beside: 0,
        grazing: u32::from(grazing),
        astray: 0,
        feature_crossings: 0,
        recess: (job.corridors && !job.short_profile) || job.slot,
        directed: job.directed,
        inside,
        features_measured: false,
        fixed_measured: false,
        ignore_furniture: false,
        aware: true,
        text: 0,
        outside: u32::from(outside),
        bounds,
        conflict_ink: OnceCell::new(),
    };
    c.astray = astray(job, &c);
    if let Some((view, proof)) = job.profile_proof {
        // An invented or hidden station is never a diameter attachment, even
        // when every available real route has another reported obstruction.
        if !proof.touches(view, tip) {
            return None;
        }
        c.hard += u32::from(!proof.profile_clear(view, &c.placed()));
    }
    Some(c)
}

// Annotation contacts are the expensive tier. Every retained candidate is fully measured;
// lower bounds allow candidates that cannot enter the sorted prefix to skip these tests.
fn measure_fixed(c: &mut Candidate, job: &Job, fixed: &Fixed) {
    let (tip, elbow, end, lb, bounds) = (c.tip, c.elbow, c.end, c.label_box, c.bounds);
    let segs = [[tip, elbow], [elbow, end]];
    let mut hard = 0;
    let padded = [
        lb[0] - LABEL_PAD,
        lb[1] - LABEL_PAD,
        lb[2] + LABEL_PAD,
        lb[3] + LABEL_PAD,
    ];
    // New automatic slot groups keep the finished audit's text gap while
    // other drawings retain their existing text-routing policy.
    let stroke_pad = job
        .stroke_pad
        .max(if job.footprints.automatic_slot_layout() {
            super::contacts::TEXT_CLEAR
        } else {
            0.
        });
    let stroke_box = grow(lb, stroke_pad);
    let near = grow(bounds, job.stroke_pad.max(LABEL_PAD));
    // The items whose lines the leader crosses, and those whose ink and the label's text meet
    // (a line through either label, or the labels together): each pair one problem, as the
    // plan's collision checks report them.
    let mut crossed: Vec<usize> = Vec::new();
    let mut text: Vec<usize> = Vec::new();
    let mut clearance = Vec::new();
    let mut furniture = Vec::new();
    let mut furniture_hard = 0;
    let mut furniture_text = Vec::new();
    let mut besides = Vec::new();
    let lines = segs.map(Line::new);
    // A hole's own centre mark is looked for as far off as the drawing's check reads a leader
    // as running along it.
    let wide = grow(
        bounds,
        job.stroke_pad
            .max(LABEL_PAD)
            .max(super::contacts::ALONG_GAP),
    );
    fixed.grid.near(0, wide, |n| {
        let (i, f) = &fixed.segments[n];
        if !segment_near(*f, wide) {
            return;
        }
        // A centre mark through the hole's centre, its own or another hole's on the same line,
        // is where a radial leader attaches. Its own (the cross centred there) shared with a
        // larger coaxial circle than the callout's own outermost one ([`Job::reach`]) runs on
        // past that circle's overrun (#104).
        let site = (fixed.crosses.contains(i) && !job.sites.is_empty())
            .then(|| nearest(&job.sites, tip))
            .filter(|s| through(*f, s.0));
        let shared = fixed.furniture.contains_key(i)
            && site.is_some_and(|(c, r, _)| {
                let mid = [(f[0][0] + f[1][0]) / 2., (f[0][1] + f[1][1]) / 2.];
                let reach = job.reach.max(r);
                (mid[0] - c[0]).hypot(mid[1] - c[1]) < 1e-3
                    && (f[1][0] - f[0][0]).hypot(f[1][1] - f[0][1]) / 2.
                        > reach + crate::centrelines::overrun(reach) + 1e-3
            });
        let close = segment_near(*f, near);
        if !close && !shared {
            return;
        }
        // Exact collinear travel stays hard; a shaft's near-parallel travel is weighted
        // ([`BESIDE`]).
        let mut beside = false;
        let mut collinear = false;
        let meets = if !close {
            beside = super::contacts::along_centreline(segs[0], *f);
            beside
        } else if let Some(&global) = fixed.furniture.get(i) {
            // The shaft only: a short shelf beside a stroke's end is not a run along it. A
            // radial leader leaving its hole along a centre mark through the hole's centre is
            // its attachment, not travel along another feature's line, outside a corridor
            // route. A shared mark runs on, though, and a shaft the finished drawing's check
            // reads as running along it ([`super::contacts::along_centreline`]) is weighed as
            // beside it; that check's length exceeds an overrun, so a mark ending at its own
            // never trips it.
            beside = (shared && super::contacts::along_centreline(segs[0], *f))
                || (crate::centrelines::runs_beside(segs[0], *f)
                    && !(site.is_some() && !job.corridors));
            collinear = lines.iter().any(|s| s.collinear(*f));
            beside
                || collinear
                || segs
                    .iter()
                    .enumerate()
                    .any(|(k, s)| crate::centrelines::leader_conflict(*s, *f, global, k == 0))
        } else {
            lines.iter().any(|s| s.meets(*f))
        };
        if meets {
            if fixed.furniture.contains_key(i) {
                furniture.push(*i);
                if beside {
                    besides.push(*i);
                }
                if collinear {
                    hard += 1;
                    furniture_hard += 1;
                }
            } else {
                crossed.push(*i);
            }
        }
        let through_text = crosses(*f, lb);
        if !through_text && !fixed.furniture.contains_key(i) && crosses(*f, stroke_box) {
            clearance.push(*i);
        }
        if through_text {
            text.push(*i);
            if fixed.furniture.contains_key(i) {
                hard += 1;
                furniture_hard += 1;
                furniture_text.push(*i);
            }
        }
    });
    fixed.grid.near(2, near, |n| {
        let (i, e) = &fixed.heads[n];
        if segment_near(*e, near) {
            if crosses(*e, lb) {
                text.push(*i);
            } else if stroke_pad > 0. && crosses(*e, stroke_box) {
                clearance.push(*i);
            }
        }
    });
    let own_head = head(arrow(tip, elbow));
    // A label clear of the padded label and of every line's box is met by neither: the tests
    // below need not run on it.
    let reach = segs.iter().chain(&own_head).flatten().fold(padded, |b, p| {
        [
            b[0].min(p[0]),
            b[1].min(p[1]),
            b[2].max(p[0]),
            b[3].max(p[1]),
        ]
    });
    fixed.grid.near(1, reach, |n| {
        let (i, l) = &fixed.labels[n];
        if finite(reach) && finite(*l) && !touches(reach, *l) {
            return;
        }
        if boxes_overlap(padded, *l) || segs.iter().chain(&own_head).any(|s| crosses(*s, *l)) {
            text.push(*i);
            if fixed.furniture.contains_key(i) {
                hard += 1;
                furniture_hard += 1;
                furniture_text.push(*i);
            }
        }
    });
    // An item both crossed and met by the label counts in each: the plan reports a crossing
    // and a text contact as two problems of the pair.
    for v in [
        &mut crossed,
        &mut text,
        &mut clearance,
        &mut furniture,
        &mut furniture_text,
        &mut besides,
    ] {
        v.sort_unstable();
        v.dedup();
    }
    let crossings = (crossed.len() + text.len()) as u32;
    let model_run = {
        let run = OnceCell::new();
        move || {
            *run.get_or_init(|| {
                job.model
                    .is_some_and(|model| segs.iter().any(|s| model.runs_beside(*s)))
            })
        }
    };
    if job.slot {
        // A slot callout's leader may land on any of its slots: one that runs along a
        // location's extension line (to the slot that line locates) reads as that line, and
        // the leader to another slot is taken instead ([`slot_callout`]).
        hard += fixed
            .extensions
            .iter()
            .filter(|(_, e)| segs.iter().any(|s| crate::centrelines::runs_beside(*s, *e)))
            .count() as u32;
    }
    if job.corridors {
        // The same finite 1 mm extension corridor used by the route acceptance
        // check participates in the initial solve, before a route becomes fixed.
        hard += fixed.extension_index.contacts(&segs, lb).0 as u32;
        if job.short_profile {
            hard += fixed
                .extensions
                .iter()
                .filter(|(_, e)| {
                    let bounds = grow(segment_bounds(*e), 0.2);
                    own_head.iter().any(|h| touches_box(*h, bounds))
                })
                .count() as u32;
        }
        if model_run() {
            hard += 1;
        }
    }
    c.hard += hard;
    c.crossings = crossings;
    c.furniture = furniture.len() as u32;
    c.furniture_hard = furniture_hard;
    c.furniture_text = furniture_text.len() as u32;
    c.beside = besides.len() as u32 + u32::from(job.silhouette.is_none() && model_run());
    c.text = text.len() as u32;
    c.clearance = clearance.len() as u32;
    c.fixed_measured = true;
    if c.ignore_furniture {
        c.without_furniture();
    }
}

/// The candidates of the clear-route sweep from *tip* (see [`SWEEP_DIRECTIONS`]): tip, elbow
/// and label side, the label on the side the leader runs to (either for a vertical one). The
/// sites are taken nearest *tip* first, so the sweep starts where the leader is.
fn sweep(job: &Job, tip: P2) -> Vec<(P2, P2, bool)> {
    let mut sites: Vec<Site> = job.sites.clone();
    let d = |s: &Site| (s.0[0] - tip[0]).hypot(s.0[1] - tip[1]);
    sites.sort_by(|a, b| d(a).total_cmp(&d(b)));
    let mut out = Vec::new();
    for site in sites.into_iter().take(SWEEP_SITES) {
        let (c, r, _) = site;
        for j in 0..SWEEP_DIRECTIONS {
            let a = std::f64::consts::TAU * j as f64 / SWEEP_DIRECTIONS as f64;
            let u = [a.cos(), a.sin()];
            let tip = rim_tip(site, [c[0] + (r + 1.0) * u[0], c[1] + (r + 1.0) * u[1]]);
            for reach in SWEEP_REACH {
                let elbow = [tip[0] + reach * u[0], tip[1] + reach * u[1]];
                let dx = elbow[0] - tip[0];
                if dx >= -1e-9 {
                    out.push((tip, elbow, true));
                }
                if dx <= 1e-9 {
                    out.push((tip, elbow, false));
                }
            }
        }
    }
    out
}

/// How many routes of the clear-route sweep (ISSUE-1187-a) the planned callout *id* has that are
/// clear of the page's furniture, the views and every other planned dimension's ink. A
/// diagnostic measurement, extended with proven interior routes. Final repair uses these rays
/// with additional hidden-edge, extension-clearance and per-kind problem budgets; this count
/// deliberately retains routes that those extra budgets may reject. None for an unplaced or
/// non-feature callout.
pub fn clear_routes(
    analysis: &crate::model::Analysis,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    dims: &[super::PlannedDimension],
    id: &str,
) -> Option<usize> {
    let d = dims.iter().find(|d| d.id == id && d.kind == "callout")?;
    let p = d.placed.as_ref()?;
    let view = d.view.strip_prefix("v:")?;
    let (mut wants, ..) = super::wants(analysis);
    let frames: BTreeMap<&str, Frame> = views
        .iter()
        .filter_map(|v| {
            let name = v.id.strip_prefix("v:")?;
            (super::NAMES.contains(&name) || crate::detail::NAMES.contains(&name))
                .then(|| (name, Frame::new(analysis, v)))
        })
        .collect();
    super::detail_routes(analysis, &mut wants, views);
    super::route(&mut wants, &frames, analysis);
    let w = wants.iter().find(|w| w.id == id)?;
    let Shape::Callout { sites, arcs } = &w.shape else {
        return None;
    };
    let f = frames.get(view)?;
    let page = Page::new(analysis, sheet, views);
    let ink = label_ink(&d.terms, font());
    let footprints = Footprints::new(analysis, f, view);
    let proofs = super::interior::proofs(analysis, views);
    let tip = p.lines.first()?[0];
    let radius = views
        .iter()
        .find(|v| v.id == d.view)
        .and_then(|v| radius_route_evidence(analysis, d, v));
    let sites = radius.as_ref().map_or_else(
        || page_sites(f, sites, arcs),
        |arcs| radius_route_sites(arcs, tip),
    );
    let job = Job {
        side: None,
        view,
        footprints: &footprints,
        outline: f.outline(),
        area: page.area(view),
        sites,
        radial: !crate::nominal::comparison()
            && w.measurements.iter().any(|m| {
                m.parameter == "slot_end_radius.radius"
                    && crate::nominal::format(m.value)
                        .split_once('.')
                        .is_some_and(|(_, tail)| tail.len() > 1)
            }),
        silhouette: grazing(f, w),
        model: None,
        short_profile: false,
        inward: inward_callout(w),
        edge_attachment: true,
        interior_proof: views
            .iter()
            .find(|v| v.id == d.view)
            .and_then(|v| proofs.get(&v.id).map(|proof| (v, proof.as_ref()))),
        profile_proof: None,
        directed: false,
        corridors: false,
        slot: false,
        steer: std::cell::Cell::new(None),
        ink_min: ink.min,
        ink_max: ink.max,
        stroke_pad: stroke_clearance(&d.terms),
        reach: own_reach(&w.measurements, f.scale()),
        beyond: [CLEAR; 4],
    };
    let rest = || {
        dims.iter()
            .enumerate()
            .filter(|(_, o)| o.id != id)
            .filter_map(|(i, o)| Some((i, o.placed.as_ref()?)))
    };
    // Centre marks and cutting-plane furniture live on views, not in `dims`.
    // They must constrain the diagnostic too, particularly a new interior label.
    let furniture = crate::centrelines::obstacles(views);
    let mut fixed = Fixed::new(
        rest().chain(
            furniture
                .iter()
                .enumerate()
                .filter_map(|(i, d)| Some((dims.len() + i, d.placed.as_ref()?))),
        ),
    );
    fixed.furniture = furniture
        .iter()
        .enumerate()
        .map(|(i, d)| (dims.len() + i, d.basis == "global_axis"))
        .collect();
    fixed.crosses = furniture
        .iter()
        .enumerate()
        .filter(|(_, d)| d.basis == "cross")
        .map(|(i, _)| dims.len() + i)
        .collect();
    Some(
        sweep(&job, tip)
            .into_iter()
            .chain(inward_rays(&job, tip))
            .filter(|(tip, elbow, _)| {
                radius
                    .as_ref()
                    .is_none_or(|arcs| arcs.iter().any(|arc| arc.accepts([*tip, *elbow])))
            })
            .filter_map(|(tip, elbow, right)| {
                let mut c = measure_bounds(&job, tip, elbow, right, &page)?;
                let interior = boxes_overlap(c.label_box, job.outline);
                let mut measured_job = job.clone();
                if interior {
                    measured_job.stroke_pad = measured_job.stroke_pad.max(LABEL_PAD);
                }
                measure_fixed(&mut c, &measured_job, &fixed);
                c.measure_features(&job);
                if interior {
                    // New interior candidates keep the final-inventory text and
                    // extension gaps; a label near a stroke is not a clear route.
                    let near = grow(c.label_box, LABEL_PAD);
                    fixed.grid.near(0, near, |n| {
                        if crosses(fixed.segments[n].1, near) {
                            c.hard += 1;
                        }
                    });
                    c.hard += u32::from(
                        fixed.extension_index.contacts(&c.segments(), c.label_box) != (0, 0),
                    );
                }
                Some(c)
            })
            .filter(Candidate::clear)
            .count(),
    )
}

/// Whether boxes *a* and *b* meet or come within a hair of each other: a pre-check that every
/// overlap ([`boxes_overlap`]) and every segment through a box ([`crosses`]) within *a* passes.
fn touches(a: [f64; 4], b: [f64; 4]) -> bool {
    const HAIR: f64 = 1e-6;
    a[0] <= b[2] + HAIR && a[2] >= b[0] - HAIR && a[1] <= b[3] + HAIR && a[3] >= b[1] - HAIR
}

fn segment_near(s: [P2; 2], b: [f64; 4]) -> bool {
    s[0][0].max(s[1][0]) >= b[0]
        && s[0][0].min(s[1][0]) <= b[2]
        && s[0][1].max(s[1][1]) >= b[1]
        && s[0][1].min(s[1][1]) <= b[3]
}

/// The distance within which two parallel lines read as one, and how long they must run together
/// to, mm.
const ALONG: f64 = 1.0;
const ALONG_RUN: f64 = 0.5;

/// Whether two lines' ink meets: they cross ([`segments_cross`]), or run along each other (a
/// leader laid on an extension line or another leader, [`ALONG`] apart for [`ALONG_RUN`] or more).
pub(super) fn lines_meet(a: [P2; 2], b: [P2; 2]) -> bool {
    may_meet(a, b) && (segments_cross(a, b) || segments_along(a, b))
}

/// Conservative broad phase for both crossing and near-parallel ink. An along
/// contact has a point within ALONG of the other segment, so its coordinate
/// bounds cannot be farther apart. Keep a numerical hair beyond that distance.
fn may_meet(a: [P2; 2], b: [P2; 2]) -> bool {
    (0..2).all(|k| {
        let lo = a[0][k].min(a[1][k]).max(b[0][k].min(b[1][k]));
        let hi = a[0][k].max(a[1][k]).min(b[0][k].max(b[1][k]));
        !lo.is_finite() || !hi.is_finite() || lo <= hi + ALONG + 1e-6
    })
}

/// Whether two segments run along each other: parallel, within [`ALONG`], overlapping by
/// [`ALONG_RUN`] or more.
pub(super) fn segments_along(a: [P2; 2], b: [P2; 2]) -> bool {
    Line::new(a).along(b)
}

/// A line to test against many others ([`lines_meet`]), its direction worked out once.
#[derive(Clone, Debug)]
pub(super) struct Line {
    a: [P2; 2],
    /// Its unit direction and length; none when it is shorter than a nanometre.
    dir: Option<(P2, f64)>,
}

impl Line {
    pub(super) fn new(a: [P2; 2]) -> Line {
        let d = [a[1][0] - a[0][0], a[1][1] - a[0][1]];
        let len = d[0].hypot(d[1]);
        let dir = if len < 1e-9 {
            None
        } else {
            Some(([d[0] / len, d[1] / len], len))
        };
        Line { a, dir }
    }

    /// [`lines_meet`] of this line and *b*.
    pub(super) fn meets(&self, b: [P2; 2]) -> bool {
        may_meet(self.a, b) && (segments_cross(self.a, b) || self.along(b))
    }

    fn collinear(&self, b: [P2; 2]) -> bool {
        let Some((u, _)) = self.dir else {
            return false;
        };
        let v = [b[1][0] - b[0][0], b[1][1] - b[0][1]];
        (u[0] * v[1] - u[1] * v[0]).abs() <= 1e-9 * v[0].hypot(v[1]) && self.along(b)
    }

    /// [`segments_along`] of this line and *b*.
    fn along(&self, b: [P2; 2]) -> bool {
        let (a, Some((u, len))) = (self.a, self.dir) else {
            return false;
        };
        let off = |p: P2| (p[0] - a[0][0]) * u[1] - (p[1] - a[0][1]) * u[0];
        let at = |p: P2| (p[0] - a[0][0]) * u[0] + (p[1] - a[0][1]) * u[1];
        if off(b[0]).abs() > ALONG || off(b[1]).abs() > ALONG {
            return false;
        }
        let (t0, t1) = (at(b[0]).min(at(b[1])), at(b[0]).max(at(b[1])));
        t1.min(len) - t0.max(0.0) >= ALONG_RUN
    }
}

/// Whether the line of segment *s* passes within a micrometre of *p*.
fn through(s: [P2; 2], p: P2) -> bool {
    let u = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
    let n = u[0].hypot(u[1]);
    n > 1e-9 && ((p[0] - s[0][0]) * u[1] - (p[1] - s[0][1]) * u[0]).abs() / n < 1e-3
}

// SVG endpoint rounding can turn an analytic endpoint touch into a crossing.
// Use the exported-ink product predicate for those curves, independently of the
// router's analytic endpoint tolerance.
fn exported_cross(a: [P2; 2], b: [P2; 2]) -> bool {
    let side = |p: P2, q: P2, r: P2| (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    side(a[0], a[1], b[0]) * side(a[0], a[1], b[1]) < -1e-12
        && side(b[0], b[1], a[0]) * side(b[0], b[1], a[1]) < -1e-12
}

/// Whether two segments cross at a point inside both (touching ends and collinear runs do not
/// count).
pub(super) fn segments_cross(a: [P2; 2], b: [P2; 2]) -> bool {
    let orient =
        |p: P2, q: P2, r: P2| (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    let eps = 1e-9;
    let (d1, d2) = (orient(b[0], b[1], a[0]), orient(b[0], b[1], a[1]));
    let (d3, d4) = (orient(a[0], a[1], b[0]), orient(a[0], a[1], b[1]));
    ((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps))
        && ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps))
}

/// How badly two candidates' ink conflicts (`_candidate_conflict`), in crossings: two when it
/// touches text (labels within [`LABEL_PAD`] of each other, or a line of one through the other's
/// label: a hard finding, `annotation_ink_overlap`), one when only their lines cross (Policy B
/// keeps that, `annotation_leader_crossing`), none when they are clear.
fn conflict(a: &Candidate, b: &Candidate) -> u32 {
    let directed = a.directed || b.directed;
    let text = TEXT_CONFLICT + u32::from(directed);
    let (ia, ib) = (a.conflict_ink(), b.conflict_ink());
    if !boxes_overlap(ia.bounds, ib.bounds) {
        return 0;
    }
    if boxes_overlap(ia.label, b.label_box) {
        return text;
    }
    if ia.segments.iter().any(|s| crosses(*s, b.label_box))
        || ib.segments.iter().any(|s| crosses(*s, a.label_box))
    {
        text
    } else if directed {
        let ab = ia.segments.iter().any(|s| {
            ib.segments
                .iter()
                .chain(std::iter::once(&arrow(b.tip, b.elbow)))
                .any(|t| lines_meet(*s, *t))
        });
        let ba = ib.segments.iter().any(|s| {
            ia.segments
                .iter()
                .chain(std::iter::once(&arrow(a.tip, a.elbow)))
                .any(|t| lines_meet(*s, *t))
        });
        u32::from(ab) + u32::from(ba)
    } else {
        u32::from(
            ia.lines
                .iter()
                .any(|s| ib.segments.iter().any(|t| s.meets(*t))),
        )
    }
}

/// The crossings a conflict that touches text counts (see [`conflict`]): two, so two leaders
/// cross rather than one runs through the other's label, as draftwright ranks them (a text
/// contact is in `_HARD_LAYOUT_CODES`, a leader crossing is Policy B info). Only between two
/// leaders, which can both move: weighing the fixed ink's text the same was measured and moves
/// more leaders across lines than it clears off labels (`docs/quality-2026-10-09.md`, "Accepted
/// regressions").
const TEXT_CONFLICT: u32 = 2;

/// The joint assignment: one candidate index per inventory (none for an empty one), minimising the
/// sum of the chosen candidates' scores plus [`CROSSING`] per crossing between the chosen
/// ([`conflict`]). Jobs are grouped by possible conflicts; each group starts from its greedy
/// assignment (jobs in order, each the best given those before it) and is then searched exactly,
/// best first, up to [`NODE_BUDGET`] nodes; the best found is kept, and the groups whose search
/// hit that bound are returned too (their members' job indices). Deterministic: ties keep the earlier job's and candidate's
/// order.
fn assign(inventories: &[Vec<Candidate>]) -> (Vec<usize>, Vec<Vec<usize>>) {
    let n = inventories.len();
    // Pairwise conflicts, per pair of jobs that have any.
    let mut pairs: BTreeMap<(usize, usize), Vec<Vec<u32>>> = BTreeMap::new();
    let mut parent: Vec<usize> = (0..n).collect();
    fn root(parent: &mut [usize], i: usize) -> usize {
        let mut r = i;
        while parent[r] != r {
            r = parent[r];
        }
        parent[i] = r;
        r
    }
    for a in 0..n {
        for b in a + 1..n {
            let m: Vec<Vec<u32>> = inventories[a]
                .iter()
                .map(|ca| inventories[b].iter().map(|cb| conflict(ca, cb)).collect())
                .collect();
            if m.iter().flatten().any(|x| *x > 0) {
                pairs.insert((a, b), m);
                let (ra, rb) = (root(&mut parent, a), root(&mut parent, b));
                parent[ra.max(rb)] = ra.min(rb);
            }
        }
    }
    let mut groups: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for i in 0..n {
        let r = root(&mut parent, i);
        groups.entry(r).or_default().push(i);
    }
    let mut choice = vec![0usize; n];
    let mut capped = Vec::new();
    for members in groups.values() {
        let (picked, stopped) = solve_group(members, inventories, &pairs);
        for (k, &i) in members.iter().enumerate() {
            choice[i] = picked[k];
        }
        if stopped {
            capped.push(members.clone());
        }
    }
    (choice, capped)
}

/// The conflicts between a group's jobs, dense: for jobs *k* and *l* of the group (local
/// indices), `of[k * m + l][w][c]` holds, bit *d*, whether candidate *c* of *k* and candidate *d*
/// of *l* conflict by more than *w* crossings, one layer per crossing up to the pair's most;
/// none for a pair that never does. The shortlist, incumbent and furniture-only variants
/// fit in one word.
struct Conflicts {
    m: usize,
    of: Vec<Option<Vec<Vec<u64>>>>,
}

// Invariant: This compile-time assertion bounds a fixed candidate bitset, independent of input.
const _: () = assert!(KEEP + ROTATIONS.len() + 2 * SHOULDER_STEPS.len() < 64); // and the incumbent

impl Conflicts {
    fn new(
        members: &[usize],
        sizes: &[usize],
        pairs: &BTreeMap<(usize, usize), Vec<Vec<u32>>>,
    ) -> Conflicts {
        let m = members.len();
        let mut of = vec![None; m * m];
        for k in 0..m {
            for l in (0..m).filter(|&l| l != k) {
                let (a, b) = (members[k], members[l]);
                let Some(matrix) = pairs.get(&(a.min(b), a.max(b))) else {
                    continue;
                };
                let met = |c: usize, d: usize| if a < b { matrix[c][d] } else { matrix[d][c] };
                let most = matrix.iter().flatten().copied().max().unwrap_or(0);
                of[k * m + l] = Some(
                    (0..most)
                        .map(|layer| {
                            (0..sizes[k])
                                .map(|c| {
                                    (0..sizes[l])
                                        .filter(|&d| met(c, d) > layer)
                                        .fold(0, |w, d| w | 1 << d)
                                })
                                .collect()
                        })
                        .collect(),
                );
            }
        }
        Conflicts { m, of }
    }

    fn any(&self, k: usize, l: usize) -> bool {
        self.of[k * self.m + l].is_some()
    }

    /// [`CROSSING`] per crossing by which candidate *c* of job *k* and candidate *d* of job *l*
    /// conflict.
    fn cost(&self, k: usize, c: usize, l: usize, d: usize) -> i64 {
        match &self.of[k * self.m + l] {
            Some(layers) => {
                layers.iter().filter(|rows| rows[c] >> d & 1 == 1).count() as i64 * CROSSING
            }
            None => 0,
        }
    }
}

fn solve_group(
    members: &[usize],
    inventories: &[Vec<Candidate>],
    pairs: &BTreeMap<(usize, usize), Vec<Vec<u32>>>,
) -> (Vec<usize>, bool) {
    let m = members.len();
    let scores: Vec<Vec<i64>> = members
        .iter()
        .map(|&i| inventories[i].iter().map(Candidate::score).collect())
        .collect();
    let sizes: Vec<usize> = scores.iter().map(Vec::len).collect();
    let conflicts = Conflicts::new(members, &sizes, pairs);
    let pair_cost = |k: usize, c: usize, l: usize, d: usize| conflicts.cost(k, c, l, d);
    // Greedy floor.
    let mut best: Vec<usize> = Vec::with_capacity(m);
    let mut best_total = 0i64;
    for (k, row) in scores.iter().enumerate() {
        let mut pick = (i64::MAX, 0usize);
        for (c, s) in row.iter().enumerate() {
            let t = s + (0..k).map(|j| pair_cost(k, c, j, best[j])).sum::<i64>();
            if t < pick.0 {
                pick = (t, c);
            }
        }
        best.push(pick.1);
        best_total += if row.is_empty() { 0 } else { pick.0 };
    }
    if m < 2 {
        return (best, false);
    }
    // Improved by descent (each job, then each conflicting pair of jobs, re-picks its best given
    // all the others, while any improves, at most DESCENT passes): a better incumbent prunes the
    // search far sooner.
    let others = |k: usize, c: usize, skip: usize, best: &[usize]| {
        (0..m)
            .filter(|&j| j != k && j != skip)
            .map(|j| pair_cost(k, c, j, best[j]))
            .sum::<i64>()
    };
    for _ in 0..DESCENT {
        let mut improved = false;
        for k in (0..m).filter(|&k| !scores[k].is_empty()) {
            let now = scores[k][best[k]] + others(k, best[k], k, &best);
            let (t, c) = (0..scores[k].len())
                .map(|c| (scores[k][c] + others(k, c, k, &best), c))
                .min()
                // Invariant: The inventory builder retains its incumbent candidate even when no trial improves it.
                .expect("a non-empty inventory");
            if t < now {
                best[k] = c;
                best_total += t - now;
                improved = true;
            }
        }
        for k in (0..m).filter(|&k| !scores[k].is_empty()) {
            for l in (k + 1..m).filter(|&l| !scores[l].is_empty()) {
                if !conflicts.any(k, l) {
                    continue;
                }
                // The others' part of each job's cost, which the pair's own choice leaves alone.
                let rest_k: Vec<i64> = (0..scores[k].len())
                    .map(|c| others(k, c, l, &best))
                    .collect();
                let rest_l: Vec<i64> = (0..scores[l].len())
                    .map(|d| others(l, d, k, &best))
                    .collect();
                let joint = |c: usize, d: usize| {
                    scores[k][c] + scores[l][d] + pair_cost(k, c, l, d) + rest_k[c] + rest_l[d]
                };
                let now = joint(best[k], best[l]);
                let mut pick = (now, best[k], best[l]);
                for c in 0..scores[k].len() {
                    for d in 0..scores[l].len() {
                        let t = joint(c, d);
                        if t < pick.0 {
                            pick = (t, c, d);
                        }
                    }
                }
                if pick.0 < now {
                    best[k] = pick.1;
                    best[l] = pick.2;
                    best_total += pick.0 - now;
                    improved = true;
                }
            }
        }
        if !improved {
            break;
        }
    }
    // Exact search, bounded.
    let mut search = Search::new(&scores, &conflicts, best, best_total);
    search.from(0, 0);
    (search.best, search.nodes >= NODE_BUDGET)
}

/// The joint assignment's exact search, best first and bounded by [`NODE_BUDGET`] nodes. At each
/// job its candidates are taken in order of their score plus [`CROSSING`] per crossing with the
/// candidates chosen before (ties by index), generated lazily: the candidates with the same
/// number of crossings are in their ranked order already, so the next is the least of each
/// count's first, and a job whose bound fails after a few never orders the rest.
struct Search {
    /// Per job, its candidates' indices in order of (score, index), and their scores so.
    ranked: Vec<Vec<usize>>,
    ranked_scores: Vec<Vec<i64>>,
    /// Per job, each earlier job it can conflict with, once per layer of [`Conflicts`], and per
    /// candidate of that job, the ranks of this job's candidates it conflicts with by more than
    /// that layer.
    before: Vec<Vec<(usize, Vec<u64>)>>,
    /// The remaining jobs' best scores, from each job on.
    rest: Vec<i64>,
    /// Per job, scratch: its ranks not yet taken, by their number of conflicts.
    rem: Vec<Vec<u64>>,
    current: Vec<usize>,
    best: Vec<usize>,
    best_total: i64,
    nodes: usize,
    /// A digest of the nodes tried, in order (the tests compare it).
    #[cfg(test)]
    trail: u64,
}

impl Search {
    /// The search of a group with these *scores* and *conflicts*, from the incumbent *best*.
    fn new(
        scores: &[Vec<i64>],
        conflicts: &Conflicts,
        best: Vec<usize>,
        best_total: i64,
    ) -> Search {
        let m = scores.len();
        let sizes: Vec<usize> = scores.iter().map(Vec::len).collect();
        // The remaining jobs' best scores bound what is left.
        let floor: Vec<i64> = scores
            .iter()
            .map(|s| s.iter().copied().min().unwrap_or(0))
            .collect();
        let mut rest = vec![0i64; m + 1];
        for k in (0..m).rev() {
            rest[k] = rest[k + 1] + floor[k];
        }
        // Each job's candidates ranked by (score, index), and each earlier conflicting job's
        // conflicts with them as words over those ranks.
        let ranked: Vec<Vec<usize>> = scores
            .iter()
            .map(|s| {
                let mut r: Vec<usize> = (0..s.len()).collect();
                r.sort_by_key(|&c| (s[c], c));
                r
            })
            .collect();
        let (sizes, ranked_ref) = (&sizes, &ranked);
        let before: Vec<Vec<(usize, Vec<u64>)>> = (0..m)
            .map(|k| {
                (0..k)
                    .filter_map(|j| conflicts.of[j * m + k].as_ref().map(|layers| (j, layers)))
                    .flat_map(|(j, layers)| {
                        layers.iter().map(move |rows| {
                            let words = (0..sizes[j])
                                .map(|cj| {
                                    (0..sizes[k])
                                        .filter(|&r| rows[cj] >> ranked_ref[k][r] & 1 == 1)
                                        .fold(0, |x, r| x | 1 << r)
                                })
                                .collect();
                            (j, words)
                        })
                    })
                    .collect()
            })
            .collect();
        Search {
            ranked_scores: (0..m)
                .map(|k| ranked[k].iter().map(|&c| scores[k][c]).collect())
                .collect(),
            rem: before.iter().map(|b| vec![0; b.len() + 2]).collect(),
            ranked,
            before,
            rest,
            current: Vec::with_capacity(m),
            best,
            best_total,
            nodes: 0,
            #[cfg(test)]
            trail: 0,
        }
    }

    fn from(&mut self, k: usize, total: i64) {
        let m = self.ranked.len();
        if k == m {
            if total < self.best_total {
                self.best_total = total;
                self.best.clone_from(&self.current);
            }
            return;
        }
        let size = self.ranked[k].len();
        if size == 0 {
            self.current.push(0);
            self.from(k + 1, total);
            self.current.pop();
            return;
        }
        // The ranks with at least n crossings, then with exactly n.
        let rem = &mut self.rem[k];
        rem.fill(0);
        rem[0] = u64::MAX >> (64 - size);
        for (i, (j, words)) in self.before[k].iter().enumerate() {
            let w = words[self.current[*j]];
            for n in (1..=i + 1).rev() {
                rem[n] |= rem[n - 1] & w;
            }
        }
        for n in 0..rem.len() - 1 {
            rem[n] &= !rem[n + 1];
        }
        loop {
            let mut next: Option<(i64, usize, usize, usize)> = None;
            for (n, &w) in self.rem[k].iter().enumerate() {
                if w == 0 {
                    continue;
                }
                let r = w.trailing_zeros() as usize;
                let (t, c) = (
                    self.ranked_scores[k][r] + n as i64 * CROSSING,
                    self.ranked[k][r],
                );
                if next.is_none_or(|(bt, bc, _, _)| (t, c) < (bt, bc)) {
                    next = Some((t, c, n, r));
                }
            }
            let Some((t, c, n, r)) = next else {
                break;
            };
            self.rem[k][n] &= !(1 << r);
            if self.nodes >= NODE_BUDGET {
                return;
            }
            self.nodes += 1;
            #[cfg(test)]
            {
                self.trail = self
                    .trail
                    .wrapping_mul(1_000_003)
                    .wrapping_add((k * 64 + c) as u64);
            }
            if total + t + self.rest[k + 1] >= self.best_total {
                break;
            }
            self.current.push(c);
            self.from(k + 1, total + t);
            self.current.pop();
        }
    }
}

fn inward_callout(w: &Want) -> bool {
    w.measurements
        .iter()
        .any(|m| m.parameter.ends_with(".diameter") || m.parameter.starts_with("thread."))
}

/// A diameter may approach its rim from within a proven face, such as a blind
/// hole floor. Select the rim with the existing producer, then travel inward.
fn inward_rays(job: &Job, anchor: P2) -> Vec<(P2, P2, bool)> {
    if !job.inward || job.interior_proof.is_none() {
        return Vec::new();
    }
    let mut sites = job.sites.clone();
    let distance = |s: &Site| (s.0[0] - anchor[0]).hypot(s.0[1] - anchor[1]);
    sites.sort_by(|a, b| distance(a).total_cmp(&distance(b)));
    let mut out = Vec::new();
    for site in sites.into_iter().take(SWEEP_SITES) {
        let (centre, r, span) = site;
        if span.is_some()
            || 2. * r <= MIN_LEADER + 1.
            || (job.ink_max[0] - job.ink_min[0]).max(job.ink_max[1] - job.ink_min[1]) > 2. * r - 1.
        {
            continue;
        }
        for i in 0..SWEEP_DIRECTIONS {
            let a = std::f64::consts::TAU * i as f64 / SWEEP_DIRECTIONS as f64;
            let u = [a.cos(), a.sin()];
            let tip = rim_tip(
                site,
                [centre[0] + (r + 1.) * u[0], centre[1] + (r + 1.) * u[1]],
            );
            for reach in SWEEP_REACH.into_iter().take_while(|d| *d < 2. * r - 0.5) {
                let elbow = [tip[0] - reach * u[0], tip[1] - reach * u[1]];
                if elbow[0] >= tip[0] - 1e-9 {
                    out.push((tip, elbow, true));
                }
                if elbow[0] <= tip[0] + 1e-9 {
                    out.push((tip, elbow, false));
                }
            }
        }
    }
    out
}

/// Interior candidates use the same rim/tip producer and bounded ray inventory as
/// exterior routes. Keep this producer separate from radius attachment policy (#145).
fn interior_routes(job: &Job, tip: P2, page: &Page) -> Vec<Candidate> {
    if job.interior_proof.is_none() {
        return Vec::new();
    }
    // The current tip first, then the feature's other sites: a label inside the view may be
    // clear only from another point on the same edge (a hex flat beside its apex).
    let mut tips = vec![tip];
    for &(site, ..) in &job.sites {
        if tips
            .iter()
            .all(|t: &P2| (t[0] - site[0]).hypot(t[1] - site[1]) > 0.5)
        {
            tips.push(site);
        }
    }
    tips.into_iter()
        .flat_map(|tip| sweep(job, tip).into_iter().chain(inward_rays(job, tip)))
        .filter_map(|(tip, elbow, right)| {
            let c = measure_bounds(job, tip, elbow, right, page)?;
            (boxes_overlap(c.label_box, job.outline) && c.hard == 0).then_some(c)
        })
        .collect()
}

/// None for other callouts; an empty inventory refuses to guess an unreadable
/// radius. Match drawn lint's fillet-before-blend ownership and geometric rule.
fn radius_route_evidence(
    analysis: &crate::model::Analysis,
    d: &PlannedDimension,
    view: &crate::model::PlannedView,
) -> Option<Vec<crate::radius::Projected>> {
    let parameter = if d
        .measurements
        .iter()
        .any(|m| m.parameter == "fillet.radius")
    {
        "fillet.radius"
    } else if d
        .measurements
        .iter()
        .any(|m| m.parameter == "cylindrical_profile.radius")
    {
        "cylindrical_profile.radius"
    } else if d.measurements.iter().any(|m| m.parameter == "blend.radius") {
        "blend.radius"
    } else {
        return None;
    };
    let frame = Frame::new(analysis, view);
    Some(
        d.measurements
            .iter()
            .filter(|m| m.parameter == parameter)
            .filter_map(|m| {
                crate::radius::projected(analysis, &m.feature, view.direction, |p| frame.page(p))
                    .ok()
            })
            .flatten()
            .collect(),
    )
}

/// Use the authoritative radius producer's proven points, not the older callout
/// site's truncated inventory (which may contain only oblique group members).
/// Admit each arc's middle station before adding its two middle-half endpoints.
fn radius_route_sites(arcs: &[crate::radius::Projected], anchor: P2) -> Vec<Site> {
    let mut arcs: Vec<_> = arcs.iter().filter(|a| !a.tips.is_empty()).collect();
    let distance = |a: &&crate::radius::Projected| {
        let p = a.tips[a.tips.len() / 2];
        (p[0] - anchor[0]).hypot(p[1] - anchor[1])
    };
    arcs.sort_by(|a, b| distance(a).total_cmp(&distance(b)));
    let mut sites = Vec::new();
    for station in [1, 0, 2] {
        for arc in &arcs {
            let i = match station {
                0 => 0,
                1 => arc.tips.len() / 2,
                _ => arc.tips.len() - 1,
            };
            let site = (arc.tips[i], 0.0, None);
            if !sites.contains(&site) {
                sites.push(site);
            }
            if sites.len() == SWEEP_SITES {
                return sites;
            }
        }
    }
    sites
}

/// Final repair over the clear-route sweep, with no-regression budgets.
#[allow(clippy::too_many_arguments)]
pub(super) fn settled_routes(
    analysis: &crate::model::Analysis,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    d: &PlannedDimension,
    want: Option<&Want>,
    peers: &[PlannedDimension],
    proof: Option<&super::interior::Proof>,
) -> Vec<Placed> {
    let Some(v) = views.iter().find(|v| v.id == d.view) else {
        return Vec::new();
    };
    let Some(old) = &d.placed else {
        return Vec::new();
    };
    let Some(shaft) = old.lines.first() else {
        return Vec::new();
    };
    let frame = Frame::new(analysis, v);
    let radius = radius_route_evidence(analysis, d, v);
    let page = Page::new(analysis, sheet, views);
    let footprints = Footprints::new(analysis, &frame, d.view.trim_start_matches("v:"));
    let (sites, ink_min, ink_max) = if let Some(w) = want {
        let Shape::Callout { sites, arcs } = &w.shape else {
            return Vec::new();
        };
        let ink = label_ink(&d.terms, font());
        (page_sites(&frame, sites, arcs), ink.min, ink.max)
    } else {
        // A control frame or surface label moves rigidly at its existing size.
        let b = old.label_box;
        (
            vec![(shaft[0], 0.0, None)],
            [0.0, -(b[3] - b[1]) / 2.0],
            [b[2] - b[0], (b[3] - b[1]) / 2.0],
        )
    };
    let sites = radius
        .as_ref()
        .map_or(sites, |arcs| radius_route_sites(arcs, shaft[0]));
    if sites.is_empty() {
        return Vec::new();
    }
    let model = analysis.edge_ink.as_ref().map(|ink| {
        let (lines, _) = view_ink(analysis, ink, v);
        SegmentIndex::new(lines)
    });
    let job = Job {
        side: None,
        view: d.view.trim_start_matches("v:"),
        footprints: &footprints,
        outline: frame.outline(),
        area: page.area(d.view.trim_start_matches("v:")),
        sites,
        radial: want.is_some_and(|w| {
            w.measurements.iter().any(|m| {
                m.parameter == "slot_end_radius.radius"
                    && crate::nominal::format(m.value)
                        .split_once('.')
                        .is_some_and(|(_, tail)| tail.len() > 1)
            })
        }),
        silhouette: want.and_then(|w| grazing(&frame, w)),
        model: model.as_ref(),
        corridors: false,
        slot: false,
        directed: false,
        short_profile: false,
        profile_proof: None,
        inward: want.is_some_and(inward_callout),
        edge_attachment: want.is_some(),
        interior_proof: proof.map(|p| (v, p)),
        steer: std::cell::Cell::new(None),
        ink_min,
        ink_max,
        stroke_pad: stroke_clearance(&d.terms).max(LABEL_PAD),
        reach: want.map_or(0.0, |w| own_reach(&w.measurements, frame.scale())),
        beyond: [CLEAR; 4],
    };
    let mut fixed = Fixed::new(
        peers
            .iter()
            .enumerate()
            .filter_map(|(i, d)| Some((i, d.placed.as_ref()?))),
    );
    fixed.furniture = peers
        .iter()
        .enumerate()
        .filter(|(_, d)| d.kind == "ink")
        .map(|(i, d)| (i, d.basis == "global_axis"))
        .collect();
    fixed.crosses = peers
        .iter()
        .enumerate()
        .filter(|(_, d)| d.kind == "ink" && d.basis == "cross")
        .map(|(i, _)| i)
        .collect();
    let mut exterior = job.clone();
    exterior.interior_proof = None;
    let mut candidates: Vec<_> = sweep(&exterior, shaft[0])
        .into_iter()
        .filter_map(|(tip, elbow, right)| measure_bounds(&exterior, tip, elbow, right, &page))
        .filter(|c| !boxes_overlap(c.label_box, job.outline) && c.hard == 0)
        .collect();
    candidates.extend(interior_routes(&job, shaft[0], &page));
    // A radius may also be read from inside its arc, the shaft running toward the centre (a
    // slot's end from within the slot): the label then sits in the opening the arc bounds.
    if job.interior_proof.is_some() {
        for arc in radius.iter().flatten() {
            for &tip in &arc.tips {
                let u = arc.radial_direction(tip);
                let n = u[0].hypot(u[1]);
                if n <= 1e-12 {
                    continue;
                }
                let u = [u[0] / n, u[1] / n];
                for reach in SWEEP_REACH.into_iter().take_while(|d| *d < 2.0 * n) {
                    let elbow = [tip[0] + reach * u[0], tip[1] + reach * u[1]];
                    for right in [elbow[0] >= tip[0] - 1e-9, elbow[0] <= tip[0] + 1e-9] {
                        if let Some(c) = measure_bounds(&job, tip, elbow, right, &page)
                            && boxes_overlap(c.label_box, job.outline)
                            && c.hard == 0
                        {
                            candidates.push(c);
                        }
                    }
                }
            }
        }
    }
    // Radius evidence is independent of the old router's sites. A cheaper or
    // crossing-free route may not violate the shared #153 drawing rule.
    candidates.retain(|c| {
        radius
            .as_ref()
            .is_none_or(|arcs| arcs.iter().any(|arc| arc.accepts([c.tip, c.elbow])))
    });
    let old_extensions = fixed.extension_index.contacts(&old.lines, old.label_box);
    let resolved = analysis.edge_ink.as_ref().and_then(|ink| {
        let key = serde_json::to_vec(v).expect("view serializes");
        crate::planning::memo(analysis, "settled-route-visibility", key, || {
            resolved_route_ink(analysis, ink, v, true).map(std::rc::Rc::new)
        })
    });
    let old_lines = [
        old.lines[0],
        old.lines.get(1).copied().unwrap_or([shaft[1], shaft[1]]),
    ];
    let hidden = resolved
        .as_ref()
        .map_or(0, |r| r.hidden.route_crossings(old_lines));
    let compact_shoulders = analysis
        .features
        .iter()
        .any(|f| f.kind == "rectangular_through_notch");
    let mut near = Vec::new();
    let admissible = |c: &Candidate| {
        let extensions = fixed.extension_index.contacts(&c.segments(), c.label_box);
        c.hard == 0
            && extensions.0 <= old_extensions.0
            && extensions.1 <= old_extensions.1
            && c.text == 0
            && c.clearance == 0
            && c.furniture == 0
            && c.beside == 0
            && c.grazing == 0
            && resolved
                .as_ref()
                .is_none_or(|r| r.hidden.route_crossings(c.segments()) <= hidden)
    };
    candidates.retain_mut(|c| {
        measure_fixed(c, &job, &fixed);
        c.measure_features(&job);
        if compact_shoulders
            && c.hard == 0
            && c.text <= 1
            && c.clearance <= 1
            && c.text + c.clearance > 0
            && c.furniture == 0
            && c.beside == 0
            && c.grazing == 0
        {
            near.push((c.score(), c.tip, c.elbow, c.end[0] >= c.elbow[0]));
        }
        admissible(c)
    });
    // Refine the held bend before changing corridors. Recompute the tip on
    // the same physical attachment and reprove the entire route, rather than
    // translating an arrowhead off its arc. At most 40 local sites are tried.
    let mut local = Vec::new();
    if compact_shoulders && old.lines.len() >= 2 {
        let site = nearest(&job.sites, shaft[0]);
        let right = old.lines[1][1][0] >= shaft[1][0];
        let mut retain = |tip: P2, elbow: P2, end: P2| {
            if let Some(c) = measure_at(&job, tip, elbow, end, right, &fixed, &page)
                && admissible(&c)
                && radius
                    .as_ref()
                    .is_none_or(|arcs| arcs.iter().any(|arc| arc.accepts([c.tip, c.elbow])))
            {
                local.push(c);
            }
        };
        if radius.is_none() {
            for step in 1..=8 {
                for delta in [
                    [-0.5 * f64::from(step), 0.],
                    [0.5 * f64::from(step), 0.],
                    [0., -0.5 * f64::from(step)],
                    [0., 0.5 * f64::from(step)],
                ] {
                    let elbow = [shaft[1][0] + delta[0], shaft[1][1] + delta[1]];
                    let end = [old.lines[1][1][0] + delta[0], old.lines[1][1][1] + delta[1]];
                    retain(rim_tip(site, elbow), elbow, end);
                }
            }
        } else {
            // A radius keeps its proven ray and middle-arc tip exactly.
            let u = [shaft[1][0] - shaft[0][0], shaft[1][1] - shaft[0][1]];
            let length = u[0].hypot(u[1]);
            for step in 1..=4 {
                for distance in [-0.5 * f64::from(step), 0.5 * f64::from(step)] {
                    let delta = [distance * u[0] / length, distance * u[1] / length];
                    let elbow = [shaft[1][0] + delta[0], shaft[1][1] + delta[1]];
                    let end = [old.lines[1][1][0] + delta[0], old.lines[1][1][1] + delta[1]];
                    retain(shaft[0], elbow, end);
                }
            }
        }
        for step in 1..=4 {
            for distance in [-0.5 * f64::from(step), 0.5 * f64::from(step)] {
                let end = [
                    old.lines[1][1][0] + if right { distance } else { -distance },
                    shaft[1][1],
                ];
                if (end[0] - shaft[1][0]).abs() >= 0.75 {
                    retain(shaft[0], shaft[1], end);
                }
            }
        }
    }
    // Newly reserved opening lanes may leave a clear shaft whose standard
    // shoulder puts its label through a witness. Refine at most 64 such rays
    // with two shorter shoulders, retaining every original physical budget.
    if compact_shoulders {
        near.sort_by_key(|c| c.0);
        for (_, tip, elbow, right) in near.into_iter().take(64) {
            for shoulder in [2., 0.75] {
                let end = [
                    elbow[0] + if right { shoulder } else { -shoulder },
                    elbow[1],
                ];
                if let Some(c) = measure_at(&job, tip, elbow, end, right, &fixed, &page)
                    && admissible(&c)
                {
                    candidates.push(c);
                }
            }
        }
    }
    // A radius reads best at its arc's middle, radially: centred routes rank first.
    candidates.sort_by_key(|c| {
        (
            radius
                .as_ref()
                .is_some_and(|arcs| !arcs.iter().any(|a| a.centred([c.tip, c.elbow]))),
            c.score(),
            boxes_overlap(c.label_box, job.outline),
        )
    });
    candidates.dedup_by(|a, b| a.label_box == b.label_box && a.tip == b.tip);
    // Preserve room for broad alternatives if local sites meet a final
    // furniture or per-kind audit that the numerical route score cannot see.
    if !compact_shoulders {
        return candidates.into_iter().map(|c| c.placed()).collect();
    }
    local
        .into_iter()
        .take(20)
        .chain(candidates)
        .map(|c| c.placed())
        .collect()
}

#[cfg(test)]
mod tests {
    /// A counterbore or spotface is the callout's own outermost circle: a centre mark sized to
    /// it (CTC-02's ⌀40 THRU ⌴SF ⌀120) is the hole's own, not a coaxial circle's (#104).
    #[test]
    fn own_reach_is_the_largest_stated_diameter_at_scale() {
        use super::*;
        let m = |parameter: &str, value: f64| crate::dims::Measurement {
            feature: "f:hole:1".into(),
            parameter: parameter.into(),
            value,
        };
        let spotfaced = [
            m("bore.diameter", 40.0),
            m("spotface.diameter", 120.0),
            m("spotface.depth", 8.0),
        ];
        assert!((own_reach(&spotfaced, 0.2) - 12.0).abs() < 1e-9);
        assert!((own_reach(&[m("bore.diameter", 8.0)], 2.0) - 8.0).abs() < 1e-9);
        assert_eq!(own_reach(&[m("blend.radius", 5.0)], 1.0), 0.0);
    }

    #[test]
    fn refinement_grid_keeps_duplicate_strokes_and_boundary_contacts() {
        use super::*;
        let mut lines = vec![
            [[0.0, 0.0], [10.0, 0.0]],
            [[0.0, 0.0], [10.0, 0.0]],
            [[4.0, -20.0], [4.0, 20.0]],
            [[f64::NAN, -2.0], [3.0, 4.0]],
        ];
        let mut seed = 82;
        for _ in 0..128 {
            lines.push(std::array::from_fn(|_| {
                std::array::from_fn(|_| 100.0 * lcg(&mut seed) - 50.0)
            }));
        }
        let grid = Grid::new([
            lines.iter().map(|s| segment_bounds(*s)).collect(),
            Vec::new(),
            Vec::new(),
        ]);
        let mut queries = vec![
            ([0.0, 0.0], [2.0, 1.0, 4.0, 2.0]),
            ([10.0, 0.0], [10.0, 0.0, 10.0, 0.0]),
            ([200.0, -200.0], [199.0, -201.0, 201.0, -199.0]),
            ([f64::NAN, 0.0], [f64::NAN, 0.0, 5.0, 5.0]),
        ];
        for _ in 0..100 {
            let q = std::array::from_fn(|_| 200.0 * lcg(&mut seed) - 100.0);
            queries.push((q, [q[0], q[1], q[0] + 5.0, q[1] + 5.0]));
        }
        for (q, label) in queries {
            let pad = grow(label, LABEL_PAD);
            let at = grow([q[0], q[1], q[0], q[1]], LABEL_PAD);
            let exhaustive = (
                lines.iter().filter(|s| touches_box(**s, pad)).count(),
                lines.iter().any(|s| touches_box(*s, at)),
            );
            assert_eq!(
                model_contacts(&lines, &grid, q, label),
                exhaustive,
                "{q:?} {label:?}"
            );
        }
    }
    #[test]
    fn contact_limits_preserve_unique_owners_and_boundary_touches() {
        use super::*;
        let extensions = [
            (7, [[0.0, -2.0], [0.0, 2.0]]),
            (3, [[4.0, -2.0], [4.0, 2.0]]),
            (7, [[0.0, -2.0], [0.0, 2.0]]),
        ];
        let lines = [[[-2.0, 0.0], [6.0, 0.0]]];
        // Owner 3 touches the padded text's boundary; repeated owner 7 counts once.
        let label = [2.0, -0.5, 3.0, 0.5];
        assert_eq!(extension_contacts(&extensions, &lines, label), (2, 1));
        assert_eq!(
            extension_contacts_bounded(&extensions, &lines, label, Some((2, 1))),
            Some((2, 1))
        );
        assert_eq!(
            extension_contacts_bounded(&extensions, &lines, label, Some((1, 1))),
            None
        );
        assert_eq!(
            extension_contacts_bounded(&extensions, &lines, label, Some((2, 0))),
            None
        );
        assert!(touches_box([[2.0, 2.0], [3.0, 3.0]], [0.0, 0.0, 2.0, 2.0]));
        assert!(touches_box([[3.0, 3.0], [2.0, 2.0]], [0.0, 0.0, 2.0, 2.0]));
        assert!(!touches_box(
            [[2.0 + 1e-10, 2.0], [3.0, 3.0]],
            [0.0, 0.0, 2.0, 2.0]
        ));
    }
    use super::*;

    /// The tip lies on the rim, radial towards the elbow (`holes._rim_tip`): the leader points at
    /// the hole's centre whatever side it leaves by.
    #[test]
    fn only_the_established_path_may_touch_uncertain_material() {
        let b = [0.0, 0.0, 10.0, 10.0];
        let old = [[[5.0, 5.0], [5.0, 12.0]]];
        assert!(!adds_unknown_path([[5.0, 5.0], [5.0, 15.0]], b, &old));
        assert!(!adds_unknown_path([[5.0, 5.0], [5.0, 11.0]], b, &old));
        assert!(adds_unknown_path([[5.0, 4.0], [5.0, 15.0]], b, &old));
        assert!(adds_unknown_path([[5.0, 5.0], [6.0, 15.0]], b, &old));
        assert!(adds_unknown_path([[4.0, 5.0], [4.0, 15.0]], b, &old));
    }

    #[test]
    fn a_tip_is_on_the_rim_radial_to_the_elbow() {
        let site = ([50.0, 40.0], 6.0, None);
        for elbow in [[80.0, 40.0], [20.0, 70.0], [50.0, 10.0], [61.0, 52.0]] {
            let t = rim_tip(site, elbow);
            let r = [t[0] - 50.0, t[1] - 40.0];
            assert!((r[0].hypot(r[1]) - 6.0).abs() < 1e-9);
            let l = [elbow[0] - t[0], elbow[1] - t[1]];
            assert!((r[0] * l[1] - r[1] * l[0]).abs() < 1e-9 && r[0] * l[0] + r[1] * l[1] > 0.0);
        }
    }

    #[test]
    fn copied_arc_rounding_preserves_the_station_on_the_real_rim() {
        let sites = [(
            [50., 40.],
            6.,
            Some(crate::arc::Span {
                start: 0.,
                sweep: std::f64::consts::TAU,
            }),
        )];
        let rounded = [53.6, 44.7999];
        let tip = rounded_rim_tip(&sites, rounded, 0.001);
        assert!(((tip[0] - 50.).hypot(tip[1] - 40.) - 6.).abs() < 1e-9);
        assert!(
            ((tip[1] - 40.).atan2(tip[0] - 50.) - (rounded[1] - 40.).atan2(rounded[0] - 50.)).abs()
                < 1e-9
        );
        assert_eq!(rounded_rim_tip(&sites, [53., 44.], 0.001), [53., 44.]);
        assert_eq!(rounded_rim_tip(&sites, tip, 0.001), tip);
    }

    #[test]
    fn offset_members_keep_ties_and_label_geometry() {
        let a: crate::model::Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding": {"source_sha256":"", "part":"test", "face_count":0, "loader":"test"},
            "envelope":[[-20,-20,-20],[20,20,20]], "iso_bounds":[0,0,40,40]
        }))
        .unwrap();
        let frame = Frame::looking(&a, [0., -1., 0.], [0., 0., 1.], [0., 0.], 1.);
        let w = Want {
            id: "group".into(),
            views: vec!["front"],
            value: 2.,
            terms: vec!["2× ⌀2".into()],
            measurements: vec![],
            basis: String::new(),
            turning: None,
            shape: Shape::Callout {
                sites: vec![([0., 0., 0.], 1.), ([10., 0., 0.], 1.)],
                arcs: vec![],
            },
        };
        let held = offset_intent(&frame, &w, [15., 0.], None).unwrap();
        assert_eq!(held.lines[0][0], [11., 0.]);
        let pin = |elbow: P2| {
            offset_intent(
                &frame,
                &w,
                [elbow[0] - 11., elbow[1]],
                Some((&held, &w.terms)),
            )
            .unwrap()
        };
        let tie = pin([5., 10.]);
        assert_eq!(tie.lines[0][0], rim_tip(([10., 0.], 1., None), [5., 10.]));
        assert_eq!(
            pin([5., 10.]),
            tie,
            "ties are repeatable even when the current member is second"
        );
        let moved = pin([4., 10.]);
        assert_eq!(moved.lines[0][0], rim_tip(([0., 0.], 1., None), [4., 10.]));
        // Its new shaft points right, but the existing label to the left stays put.
        let mut single = w.clone();
        single.shape = Shape::Callout {
            sites: vec![([10., 0., 0.], 1.)],
            arcs: vec![],
        };
        let old = offset_intent(&frame, &single, [-7., 10.], Some((&held, &w.terms))).unwrap();
        assert_eq!(moved.lines[1..], old.lines[1..]);
        assert_eq!(moved.label_at, old.label_at);
        assert_eq!(moved.label_box, old.label_box);
        assert_eq!(moved.rotation, old.rotation);
    }

    #[test]
    fn a_compliant_long_radius_still_compares_clear_routes_on_both_sides() {
        use super::*;
        use crate::dims::placement;
        use crate::model::{Analysis, PlannedSheet, PlannedView, Spec};
        let station = 3. / 2f64.sqrt();
        let mut analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema":crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"test","part":"test","face_count":1,"loader":"test"},
            "envelope":[[-3.,-3.,-1.],[3.,3.,1.]],"iso_bounds":[0.,0.,0.,0.],
            "features":[{"id":"f:blend:1","kind":"blend","faces":[0],
                "parameters":{"radius":3.},"fingerprint":{"version":"test","family":"blends",
                "subtype":"blend","sizes":{},"axis":[0.,0.,1.],"position":[0.,0.,0.],"neighbourhood":[]}}],
            "radius_arcs":{"f:blend:1":[{"arc":{"centre":[0.,0.,0.],"radius":3.,
                "u":[1.,0.,0.],"v":[0.,1.,0.],"start":0.,"sweep":std::f64::consts::FRAC_PI_2},
                "axis":[0.,0.,1.],"centre_open":true,"tips":[[station,station,0.]]}]}
        })).unwrap();
        let view: PlannedView = serde_json::from_value(serde_json::json!({
            "id":"v:plan","direction":[0.,0.,1.],"up":[0.,1.,0.],"at":[50.,50.],
            "scale":1.,"area":[10.,10.,190.,180.],"default":true,"basis":"","state":{"kind":"shown"}
        }))
        .unwrap();
        let sheet: PlannedSheet = serde_json::from_value(serde_json::json!({
            "size":"A4","page":[297.,210.],"scale":"1:1","scale_factor":1.,"arrangement":"columns",
            "title_block":[200.,10.,280.,30.],"default":true,"basis":"","state":{"kind":"shown"},"title":{}
        })).unwrap();
        let tip = [50. + station; 2];
        let elbow = [tip[0] + 40. / 2f64.sqrt(), tip[1] + 40. / 2f64.sqrt()];
        let d: PlannedDimension = serde_json::from_value(serde_json::json!({
            "id":"d:r","kind":"callout","view":"v:plan","value":3.,"label":"R3","terms":["R3"],
            "measurements":[{"feature":"f:blend:1","parameter":"blend.radius","value":3.}],
            "default":true,"from_spec":[],"basis":"test","state":{"kind":"shown"},
            "placed":{"lines":[[tip,elbow],[elbow,[elbow[0]+3.,elbow[1]]]],
                "arrows":[placement::arrow(tip,elbow)],"label_at":[elbow[0]+4.,elbow[1]],
                "label_box":[elbow[0]+4.,elbow[1]-1.,elbow[0]+10.,elbow[1]+2.],"rotation":0.}
        }))
        .unwrap();
        let mut chosen = Vec::new();
        for centre_open in [true, false] {
            analysis.radius_arcs.get_mut("f:blend:1").unwrap()[0].centre_open = centre_open;
            let mut items = vec![d.clone()];
            settle_radius_leaders(
                &analysis,
                &Spec::default(),
                &sheet,
                std::slice::from_ref(&view),
                &mut items,
                &[],
                &super::super::footprints::Cache::default(),
                None,
            );
            let p = items[0].placed.as_ref().unwrap();
            assert!(
                length(&p.lines) < 12.,
                "a long radial route must not preempt clear short paper: {p:?}"
            );
            assert!(
                radius_route_evidence(&analysis, &items[0], &view)
                    .unwrap()
                    .iter()
                    .any(|a| a.accepts(p.lines[0]))
            );
            chosen.push(p.clone());
        }
        assert_eq!(
            chosen[0], chosen[1],
            "material-side metadata cannot choose the route"
        );
    }

    #[test]
    fn final_radius_alternatives_obey_the_drawn_arc_rule() {
        let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz");
        let analysis = crate::model::analyse_native(&file).unwrap().1;
        let plan = crate::model::plan(&analysis, &crate::model::Spec::default()).unwrap();
        let d = plan
            .dimensions
            .iter()
            .find(|d| {
                d.measurements.iter().any(|m| {
                    m.parameter == "fillet.radius"
                        && analysis.features.iter().any(|f| {
                            f.id == m.feature
                                && f.faces == [98]
                                && f.parameters["side"] == "concave"
                        })
                })
            })
            .unwrap();
        let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
        let mut wants = super::super::wants(&analysis).0;
        let frames = plan
            .views
            .iter()
            .filter_map(|v| {
                let name = v.id.strip_prefix("v:")?;
                super::super::NAMES
                    .contains(&name)
                    .then(|| (name, Frame::new(&analysis, v)))
            })
            .collect();
        super::super::route(&mut wants, &frames, &analysis);
        let want = wants.iter().find(|w| w.id == d.id).unwrap();
        let proof = super::super::interior::proofs(&analysis, &plan.views);
        let arcs = radius_route_evidence(&analysis, d, view).unwrap();
        assert!(
            !arcs.is_empty(),
            "the fixture proves its trimmed radius arcs"
        );
        let alternatives = settled_routes(
            &analysis,
            &plan.sheet,
            &plan.views,
            d,
            Some(want),
            &[],
            proof.get(&view.id).map(|p| p.as_ref()),
        );
        assert!(
            !alternatives.is_empty(),
            "proven end-on group members must remain searchable"
        );
        for p in alternatives {
            assert!(
                arcs.iter().any(|a| a.accepts(p.lines[0])),
                "a final route must not approach through the base plate: {:?}",
                p.lines[0]
            );
        }
    }

    #[test]
    fn a_radius_route_without_recesses_keeps_its_attachment_and_readable_geometry() {
        let corpus = std::env::var_os("DRAFTWRIGHT_CORPUS")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(|h| std::path::PathBuf::from(h).join("repos/quiddity/tests/corpus"))
            });
        let Some(corpus) = corpus.filter(|p| p.is_dir()) else {
            assert_ne!(
                std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
                Ok("1")
            );
            return;
        };
        let mut a = crate::model::analyse_native(&corpus.join("cadgenbench_inputs/cgb245.step"))
            .unwrap()
            .1;
        assert!(a.features.iter().all(|f| f.kind != "section_recess"));
        // Its two obround slots are congruent, so a slot callout would state their ends: one is
        // split from the callout, so each keeps its own radius leader.
        split_slot(&mut a, "f:slot:2");
        let plan = crate::model::plan_recovered(&a, &crate::model::Spec::default()).unwrap();
        let id = "d:f:slot:1/slot_end_radius.radius";
        let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        let p = d.placed.as_ref().unwrap();
        let v = plan.views.iter().find(|v| v.id == d.view).unwrap();
        let wants = super::super::wants(&a).0;
        let w = wants.iter().find(|w| w.id == id).unwrap();
        let Shape::Callout { sites, arcs } = &w.shape else {
            panic!("radius sites");
        };
        let sites = page_sites(&Frame::new(&a, v), sites, arcs);
        let [tip, elbow] = p.lines[0];
        assert!(sites.iter().any(
            |&(c, r, span)| ((tip[0] - c[0]).hypot(tip[1] - c[1]) - r).abs()
                < crate::lint::leaders::TOLERANCE
                && span.is_none_or(|s| s.contains((tip[1] - c[1]).atan2(tip[0] - c[0]), 1e-6))
        ));
        assert_eq!(p.rotation, 0.0);
        assert_eq!(p.arrows.len(), 1);
        assert!(length(&p.lines) <= MAX_LEADER + SHOULDER);
        assert!((elbow[0] - tip[0]) * (p.lines[1][1][0] - elbow[0]) >= -1e-9);
        assert_eq!(p.lines[1][0][1], p.lines[1][1][1]);
    }

    /// Splits slot *id* from its congruent slots' callout, as `features/<id>/split` does, so it
    /// and its twin are dimensioned singly, each radius leader its own (`dims::slots`).
    fn split_slot(a: &mut crate::model::Analysis, id: &str) {
        let f = a.features.iter_mut().find(|f| f.id == id).unwrap();
        f.parameters.insert(
            crate::lint::slots::ALONE.into(),
            serde_json::Value::Bool(true),
        );
    }

    #[test]
    fn refined_slot_radius_leaders_point_towards_the_arc_centre() {
        let corpus = std::env::var_os("DRAFTWRIGHT_CORPUS")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(|h| std::path::PathBuf::from(h).join("repos/quiddity/tests/corpus"))
            });
        let Some(corpus) = corpus.filter(|p| p.is_dir()) else {
            assert_ne!(
                std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
                Ok("1")
            );
            return;
        };
        let a = crate::model::analyse_native(&corpus.join("nist/nist_ftc_08_asme1_rc.stp"))
            .unwrap()
            .1;
        let plan = crate::model::plan_recovered(&a, &crate::model::Spec::default()).unwrap();
        let wants = super::super::wants(&a).0;
        let baseline: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/leader_routes.json")).unwrap();
        let before = &baseline["parts"]["nist_ftc_08_asme1_rc"];
        let mut checked = 0;
        for d in &plan.dimensions {
            if !d
                .measurements
                .iter()
                .any(|m| m.parameter == "slot_end_radius.radius")
            {
                continue;
            }
            let Some(p) = &d.placed else { continue };
            let w = wants.iter().find(|w| w.id == d.id).unwrap();
            let Shape::Callout { sites, arcs } = &w.shape else {
                panic!("radius needs arc sites")
            };
            let v = plan.views.iter().find(|v| v.id == d.view).unwrap();
            let sites = page_sites(&Frame::new(&a, v), sites, arcs);
            let [tip, elbow] = p.lines[0];
            let (centre, _, _) = nearest(&sites, tip);
            let r = [tip[0] - centre[0], tip[1] - centre[1]];
            let shaft = [elbow[0] - tip[0], elbow[1] - tip[1]];
            let unchanged = [tip, elbow].iter().enumerate().all(|(i, p)| {
                (0..2).all(|k| {
                    (p[k] - v.at[k] - before[&d.id]["route"][i][k].as_f64().unwrap()).abs() < 1e-6
                })
            });
            if !unchanged {
                let sine = (r[0] * shaft[1] - r[1] * shaft[0]).abs()
                    / (r[0].hypot(r[1]) * shaft[0].hypot(shaft[1]));
                assert!(
                    sine < 1e-9,
                    "{}: moved radius leader must be radial; tip={tip:?}, elbow={elbow:?}, centre={centre:?}, sites={sites:?}",
                    d.id
                );
                assert!(r[0] * shaft[0] + r[1] * shaft[1] > 0.0);
            }
            checked += 1;
        }
        assert!(checked > 0);
    }

    /// string_post_tapped's turned-step callouts start with their labels on extension lines:
    /// `ø6` touches three dimensions' extension lines, two with its text, and `ø5` two, both with
    /// its text. Refinement takes both labels off: (3, 2) -> (1, 0) and (2, 2) -> (0, 0).
    #[test]
    fn refinement_takes_callout_text_off_extension_lines() {
        use std::io::Read as _;
        let gz = include_bytes!("../../tests/fixtures/pmi/step/string_post_tapped.step.gz");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(&gz[..])
            .read_to_end(&mut bytes)
            .unwrap();
        let a = crate::model::analyse_native_step(
            std::path::Path::new("string_post_tapped.step"),
            &bytes,
        )
        .unwrap()
        .1;
        let plan = crate::model::plan_recovered(&a, &crate::model::Spec::default()).unwrap();
        let extensions: Vec<(usize, [P2; 2])> = plan
            .dimensions
            .iter()
            .enumerate()
            .filter_map(|(j, d)| Some((j, d.placed.as_ref()?)))
            .filter(|(_, p)| p.arrows.len() == 2 && p.lines.len() >= 3)
            .flat_map(|(j, p)| p.lines[..2].iter().map(move |s| (j, *s)))
            .collect();
        for (id, contacts) in [
            ("d:f:turned_step:1/step.diameter", (0, 0)),
            ("d:f:turned_step:2/step.diameter", (0, 0)),
        ] {
            let callout = plan
                .dimensions
                .iter()
                .find(|d| d.id == id)
                .and_then(|d| d.placed.as_ref())
                .unwrap();
            assert_eq!(
                extension_contacts(&extensions, &callout.lines, callout.label_box),
                contacts,
                "{id}"
            );
        }
    }

    #[test]
    fn indexed_extension_contacts_match_full_scans_and_count_distinct_owners() {
        let mut seed = 7919;
        let ink: Vec<_> = (0..300)
            .map(|n| {
                (
                    (if n % 3 == 0 { n + 150 } else { n / 2 }) * 1_000_003 + 500,
                    std::array::from_fn(|_| std::array::from_fn(|_| 200. * lcg(&mut seed) - 100.)),
                )
            })
            .collect();
        let indexed = ExtensionIndex::new(ink.clone());
        for _ in 0..500 {
            let route: [[P2; 2]; 2] = std::array::from_fn(|_| {
                std::array::from_fn(|_| std::array::from_fn(|_| 200. * lcg(&mut seed) - 100.))
            });
            let at = [200. * lcg(&mut seed) - 100., 200. * lcg(&mut seed) - 100.];
            let label = [at[0], at[1], at[0] + 12., at[1] + 4.];
            let flare = head(arrow(route[0][0], route[0][1]));
            assert_eq!(
                indexed.head_contacts(&flare),
                ink.iter()
                    .filter(|(_, e)| {
                        let bounds = grow(segment_bounds(*e), 0.2);
                        flare.iter().any(|s| touches_box(*s, bounds))
                    })
                    .count()
            );
            assert_eq!(
                indexed.contacts(&route, label),
                extension_contacts(&ink, &route, label)
            );
            let full = extension_contacts(&ink, &route, label);
            for maximum in [
                (0, 0),
                full,
                (full.0.saturating_sub(1), full.1),
                (full.0, full.1.saturating_sub(1)),
            ] {
                let expected = (full.0 <= maximum.0 && full.1 <= maximum.1).then_some(full);
                assert_eq!(
                    indexed.contacts_bounded(&route, label, Some(maximum)),
                    expected
                );
            }
            assert_eq!(
                meets_extensions(
                    &ink.iter().map(|(_, e)| *e).collect::<Vec<_>>(),
                    &route,
                    label
                ),
                extension_contacts(&ink, &route, label).0 > 0
            );
            assert_eq!(
                indexed.contacts(&[], label),
                extension_contacts(&ink, &[], label)
            );
        }
        let ink = vec![(4, [[0., 0.], [0., 20.]]), (4, [[2., 0.], [2., 20.]])];
        let index = ExtensionIndex::new(ink.clone());
        assert_eq!(index.contacts(&[], [0., 5., 2., 10.]), (1, 1));
        let sparse = vec![
            (usize::MAX, [[0., 0.], [0., 20.]]),
            (usize::MAX, [[2., 0.], [2., 20.]]),
            (1_000_007, [[1., 0.], [1., 20.]]),
        ];
        assert_eq!(
            ExtensionIndex::new(sparse.clone()).contacts(&[], [0., 5., 2., 10.]),
            extension_contacts(&sparse, &[], [0., 5., 2., 10.])
        );
    }

    #[test]
    fn shared_route_ink_and_uncertain_boxes_match_local_full_scans() {
        let mut seed = 7843;
        let ink: Vec<_> = (0..500)
            .map(|_| {
                std::array::from_fn(|_| std::array::from_fn(|_| 200.0 * lcg(&mut seed) - 100.0))
            })
            .collect();
        let region = [-20.0, -20.0, 20.0, 20.0];
        let local: Vec<_> = ink
            .iter()
            .copied()
            .filter(|s| touches_box(*s, region))
            .collect();
        let shared = SegmentIndex::new(ink.clone());
        let cropped = SegmentIndex::new(local);
        let boxes: Vec<_> = (0..100)
            .map(|_| {
                let x = 200.0 * lcg(&mut seed) - 100.0;
                let y = 200.0 * lcg(&mut seed) - 100.0;
                [x, y, x + 5.0, y + 8.0]
            })
            .collect();
        let grid = Grid::new([boxes.clone(), Vec::new(), Vec::new()]);
        let historical = [[[-30.0, 0.0], [30.0, 0.0]], [[30.0, 0.0], [35.0, 0.0]]];
        for _ in 0..150 {
            let lines: [[P2; 2]; 2] = std::array::from_fn(|_| {
                std::array::from_fn(|_| std::array::from_fn(|_| 36.0 * lcg(&mut seed) - 18.0))
            });
            let at = lines[0][0];
            let bounds = [at[0] - 1.0, at[1] - 1.0, at[0] + 1.0, at[1] + 1.0];
            assert_eq!(shared.touching(bounds), cropped.touching(bounds));
            assert_eq!(
                shared.new_crossing(&lines, &historical),
                cropped.new_crossing(&lines, &historical)
            );
            for line in lines {
                assert_eq!(shared.runs_beside(line), cropped.runs_beside(line));
            }
            for ordinary in [false, true] {
                let full = !boxes.iter().any(|b| {
                    if ordinary {
                        lines
                            .iter()
                            .enumerate()
                            .any(|(i, s)| buried(*s, *b) > buried(historical[i], *b) + 1e-6)
                    } else {
                        lines.iter().any(|s| adds_unknown_path(*s, *b, &historical))
                    }
                });
                assert_eq!(
                    unknown_route_safe(&lines, &boxes, &grid, &historical, &historical, ordinary),
                    full
                );
            }
        }
    }

    #[test]
    fn indexed_route_checks_match_full_edge_scans() {
        let mut seed = 7919;
        let mut ink: Vec<_> = (0..300)
            .map(|_| {
                std::array::from_fn(|_| std::array::from_fn(|_| 200.0 * lcg(&mut seed) - 100.0))
            })
            .collect();
        // A run assembled from short strokes must not disappear at leaf boundaries.
        ink.extend((0..40).map(|n| [[n as f64, 0.0], [n as f64 + 1.0, 0.0]]));
        let index = SegmentIndex::new(ink.clone());
        for n in 0..200 {
            let line = if n == 0 {
                [[0.0, 0.3], [40.0, 0.3]]
            } else {
                std::array::from_fn(|_| std::array::from_fn(|_| 200.0 * lcg(&mut seed) - 100.0))
            };
            assert_eq!(index.runs_beside(line), runs_beside(line, &ink));
            assert_eq!(index.runs_beside(line), runs_beside(line, &ink));
            let bounds = grow(segment_bounds(line), 1.0);
            assert_eq!(
                index.crosses_box(bounds),
                ink.iter()
                    .any(|line| crate::dims::placement::crosses(*line, bounds))
            );
            let full = ink.iter().filter(|s| touches_box(**s, bounds)).count();
            // Start with an incomplete zero-bound query, then raise and lower
            // the bound and request an exact count from the same cache entry.
            for maximum in [0, full.saturating_sub(1), full, full + 1, 0] {
                assert_eq!(index.touching_at_most(bounds, maximum), full <= maximum);
            }
            for _ in 0..2 {
                assert_eq!(
                    index.touching(bounds),
                    ink.iter().filter(|s| touches_box(**s, bounds)).count()
                );
            }
            assert_eq!(
                index
                    .near(bounds)
                    .into_iter()
                    .filter(|s| touches_box(**s, bounds))
                    .count(),
                ink.iter().filter(|s| touches_box(**s, bounds)).count()
            );
            assert_eq!(
                index.crossings(line),
                ink.iter().filter(|s| segments_cross(line, **s)).count()
            );
        }
    }

    #[test]
    fn diagonal_crossing_queries_retain_thin_and_boundary_leaves() {
        let ink: Vec<_> = (0..2048)
            .map(|n| {
                let x = (n % 64) as f64;
                let y = (n / 64) as f64;
                [[x, y], [x, y + 0.75]]
            })
            .collect();
        let index = SegmentIndex::new(ink.clone());
        for line in [
            [[-1., -1.], [65., 33.]],
            [[0., 0.], [63., 31.]],
            [[0., 0.375], [63., 0.375]],
            [[63., -1.], [63., 33.]],
            [[-1., 31.375], [65., 31.375]],
        ] {
            for _ in 0..2 {
                assert_eq!(
                    index.crossings(line),
                    ink.iter().filter(|s| segments_cross(line, **s)).count()
                );
            }
        }
    }

    #[test]
    fn indexed_leader_routes_match_full_scans_and_count_double_crossings_once() {
        let edge = [[-1., 0.5], [3., 0.5]];
        let route = [[[0., 0.], [1., 1.]], [[1., 1.], [2., 0.]]];
        let index = SegmentIndex::new(vec![edge]);
        assert!(route.iter().all(|s| segments_cross(*s, edge)));
        assert_eq!(index.route_crossings(route), 1);
        assert_eq!(index.route_crossings(route), 1);
        let mut seed = 7919;
        let ink: Vec<_> = (0..300)
            .map(|_| std::array::from_fn(|_| std::array::from_fn(|_| 200. * lcg(&mut seed) - 100.)))
            .collect();
        let index = SegmentIndex::new(ink.clone());
        for _ in 0..200 {
            let points: [P2; 3] =
                std::array::from_fn(|_| std::array::from_fn(|_| 200. * lcg(&mut seed) - 100.));
            let route = [[points[0], points[1]], [points[1], points[2]]];
            let full = ink
                .iter()
                .filter(|edge| route.iter().any(|line| segments_cross(*line, **edge)))
                .count();
            assert_eq!(index.route_crossings(route), full);
            assert_eq!(index.route_crossings(route), full);
            let old = [[[0., 0.], [25., -30.]], [[25., -30.], [-20., 15.]]];
            assert_eq!(
                index.new_crossing(&route, &old),
                ink.iter().any(|edge| {
                    route.iter().any(|line| segments_cross(*line, *edge))
                        && !old.iter().any(|line| segments_cross(*line, *edge))
                })
            );
        }
    }

    /// Stations start at the natural one, alternate outwards, stay within the side and are
    /// bounded however long the side.
    #[test]
    fn stations_are_bounded_and_nearest_first() {
        let s = stations(10.0, 0.0, 1.0e7);
        assert_eq!(s.len(), MAX_STATIONS);
        assert_eq!(&s[..3], &[10.0, 11.0, 9.0]);
        let s = stations(-5.0, 0.0, 2.5);
        assert_eq!(s, vec![0.0, 1.0, 2.0]);
        assert!(stations(0.0, 1.0, 0.0).is_empty());
    }

    #[test]
    fn a_sweep_reach_at_the_length_limit_meets_it_whatever_the_last_bit() {
        // FTC-07 AP242's profile-surface frame (#236): a 75 mm reach measured exactly 75 on Apple
        // Silicon, one unit in the last place over on x86-64 Linux, and the route was hard there.
        let tip = [193.93296474348, 416.29007182347993];
        for j in 0..SWEEP_DIRECTIONS {
            let a = std::f64::consts::TAU * j as f64 / SWEEP_DIRECTIONS as f64;
            let u = [a.cos(), a.sin()];
            let elbow = [tip[0] + MAX_LEADER * u[0], tip[1] + MAX_LEADER * u[1]];
            let shaft = (elbow[0] - tip[0]).hypot(elbow[1] - tip[1]);
            let (mut low, mut high) = (shaft, shaft);
            for _ in 0..4 {
                low = low.next_down();
                high = high.next_up();
                for length in [low, shaft, high] {
                    assert!(!too_long(length), "direction {j}: {length}");
                }
            }
            // The route's report measures the leader itself: an elbow a few last bits further
            // out still meets the limit.
            let mut far = elbow;
            for _ in 0..4 {
                far = [
                    if u[0] < 0.0 {
                        far[0].next_down()
                    } else {
                        far[0].next_up()
                    },
                    if u[1] < 0.0 {
                        far[1].next_down()
                    } else {
                        far[1].next_up()
                    },
                ];
                let problems = route_reports_named("a", "a", "front", &[], [tip, far]);
                assert!(
                    problems.iter().all(|p| p.code != "annotation_leader_long"),
                    "direction {j}"
                );
            }
        }
        assert!(too_long(MAX_LEADER + 1e-3));
        let over = [tip[0] + MAX_LEADER + 1e-3, tip[1]];
        let problems = route_reports_named("a", "a", "front", &[], [tip, over]);
        assert!(problems.iter().any(|p| p.code == "annotation_leader_long"));
    }

    fn cand(tip: P2, elbow: P2, cost: i64) -> Candidate {
        let end = [elbow[0] + SHOULDER, elbow[1]];
        let label_box = [end[0] + 1.0, elbow[1] - 1.0, end[0] + 10.0, elbow[1] + 1.5];
        Candidate {
            tip,
            elbow,
            end,
            label_at: [end[0] + 1.0, elbow[1]],
            label_box,
            cost,
            hard: 0,
            crossings: 0,
            furniture: 0,
            furniture_hard: 0,
            furniture_text: 0,
            clearance: 0,
            beside: 0,
            grazing: 0,
            astray: 0,
            feature_crossings: 0,
            recess: false,
            directed: false,
            inside: 0,
            features_measured: true,
            fixed_measured: true,
            ignore_furniture: false,
            aware: false,
            text: 0,
            outside: 0,
            conflict_ink: OnceCell::new(),
            bounds: [
                tip[0].min(elbow[0]),
                tip[1].min(elbow[1]).min(label_box[1]),
                label_box[2],
                tip[1].max(elbow[1]).max(label_box[3]),
            ],
        }
    }

    #[test]
    fn a_shared_recess_solve_counts_both_directions_of_a_shaft_crossing() {
        let mut a = cand([0., 0.], [10., 10.], 14_000);
        let b = cand([0., 10.], [10., 0.], 14_000);
        assert_eq!(conflict(&a, &b), 1);
        a.directed = true;
        assert_eq!(conflict(&a, &b), 2);
        assert_eq!(conflict(&b, &a), 2);
    }

    /// Two callouts whose cheapest leaders cross: the joint assignment gives one its second
    /// choice rather than letting them cross (ISSUE-740-a), and keeps the cheaper total.
    #[test]
    fn crossing_leaders_are_assigned_apart() {
        // Job 0's best runs up-right across job 1's best, which runs right.
        let a = vec![
            cand([0.0, 0.0], [10.0, 10.0], 14_000),
            cand([0.0, 0.0], [10.0, -10.0], 15_000),
        ];
        let b = vec![
            cand([0.0, 5.0], [20.0, 5.0], 20_000),
            cand([0.0, 5.0], [-20.0, 5.0], 30_000),
        ];
        assert_eq!(conflict(&a[0], &b[0]), 1);
        assert_eq!(conflict(&a[1], &b[0]), 0);
        assert_eq!(assign(&[a, b]), (vec![1, 0], vec![]));
    }

    /// A leader through another's label weighs two crossings, so the assignment takes a leader
    /// crossing a leader before one through a label, though it is longer.
    #[test]
    fn a_leader_through_a_label_weighs_two_crossings() {
        // Job 1's leader runs right, its label at x 18 to 27 on y 5.
        let b = vec![cand([0.0, 5.0], [15.0, 5.0], 20_000)];
        // Job 0's cheapest runs up through that label; its second crosses job 1's leader.
        let a = vec![
            cand([20.0, -5.0], [20.0, 15.0], 20_000),
            cand([10.0, -5.0], [10.0, 15.0], 21_000),
        ];
        assert_eq!(conflict(&a[0], &b[0]), TEXT_CONFLICT);
        assert_eq!(conflict(&a[1], &b[0]), 1);
        assert_eq!(assign(&[a, b]), (vec![1, 0], vec![]));
    }

    /// A small deterministic generator for the solver's tests.
    fn lcg(seed: &mut u64) -> f64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (*seed >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Random leaders fanned around a few sites, short enough to meet often.
    fn random_inventories(seed: &mut u64, jobs: usize, each: usize) -> Vec<Vec<Candidate>> {
        (0..jobs)
            .map(|j| {
                let tip = [10.0 * j as f64, 0.0];
                (0..each)
                    .map(|_| {
                        let elbow = [
                            tip[0] + 30.0 * (lcg(seed) - 0.5),
                            tip[1] + 30.0 * (lcg(seed) - 0.5),
                        ];
                        let mut c = cand(tip, elbow, (lcg(seed) * 30_000.0) as i64);
                        c.crossings = u32::from(lcg(seed) < 0.2);
                        c
                    })
                    .collect()
            })
            .collect()
    }

    fn total(inventories: &[Vec<Candidate>], choice: &[usize]) -> i64 {
        let mut t: i64 = choice
            .iter()
            .zip(inventories)
            .map(|(c, inv)| inv[*c].score())
            .sum();
        for a in 0..choice.len() {
            for b in a + 1..choice.len() {
                t += i64::from(conflict(
                    &inventories[a][choice[a]],
                    &inventories[b][choice[b]],
                )) * CROSSING;
            }
        }
        t
    }

    /// The bounded joint assignment equals an exhaustive oracle on small instances (ISSUE-740-a:
    /// the fewest crossings, then the least total length).
    #[test]
    fn the_assignment_matches_an_exhaustive_oracle() {
        let mut seed = 7u64;
        for _ in 0..40 {
            let inv = random_inventories(&mut seed, 4, 4);
            let got = assign(&inv).0;
            let mut best = i64::MAX;
            for k in 0..4usize.pow(4) {
                let choice: Vec<usize> = (0..4).map(|j| k / 4usize.pow(j) % 4).collect();
                best = best.min(total(&inv, &choice));
            }
            assert_eq!(total(&inv, &got), best);
        }
    }

    /// A group too large to search whole stops at [`NODE_BUDGET`] with an assignment no worse
    /// than the greedy floor, the same on every run (ISSUE-740-b).
    #[test]
    fn a_large_group_is_bounded_and_deterministic() {
        let mut seed = 11u64;
        let inv = random_inventories(&mut seed, 30, KEEP);
        let (a, capped) = assign(&inv);
        assert_eq!(a, assign(&inv).0);
        // The bound fired, and that is reported.
        assert_eq!(capped, vec![(0..inv.len()).collect::<Vec<_>>()]);
        // The greedy floor: each job the best given those before it.
        let mut greedy: Vec<usize> = Vec::new();
        for k in 0..inv.len() {
            let pick = (0..inv[k].len())
                .min_by_key(|&c| {
                    inv[k][c].score()
                        + (0..k)
                            .map(|j| i64::from(conflict(&inv[k][c], &inv[j][greedy[j]])))
                            .sum::<i64>()
                            * CROSSING
                })
                .unwrap();
            greedy.push(pick);
        }
        assert!(total(&inv, &a) <= total(&inv, &greedy));
    }

    /// The search as it was first written: each job's candidates fully sorted by score plus
    /// crossings with those chosen before, then tried in that order.
    #[allow(clippy::too_many_arguments)]
    fn reference_search(
        k: usize,
        total: i64,
        current: &mut Vec<usize>,
        best: &mut Vec<usize>,
        best_total: &mut i64,
        nodes: &mut usize,
        trail: &mut u64,
        scores: &[Vec<i64>],
        rest: &[i64],
        conflicts: &Conflicts,
    ) {
        if k == scores.len() {
            if total < *best_total {
                *best_total = total;
                *best = current.clone();
            }
            return;
        }
        let mut order: Vec<(i64, usize)> = (0..scores[k].len())
            .map(|c| {
                let p: i64 = (0..k).map(|j| conflicts.cost(k, c, j, current[j])).sum();
                (scores[k][c] + p, c)
            })
            .collect();
        order.sort();
        if order.is_empty() {
            current.push(0);
            reference_search(
                k + 1,
                total,
                current,
                best,
                best_total,
                nodes,
                trail,
                scores,
                rest,
                conflicts,
            );
            current.pop();
            return;
        }
        for (t, c) in order {
            if *nodes >= NODE_BUDGET {
                return;
            }
            *nodes += 1;
            *trail = trail
                .wrapping_mul(1_000_003)
                .wrapping_add((k * 64 + c) as u64);
            if total + t + rest[k + 1] >= *best_total {
                break;
            }
            current.push(c);
            reference_search(
                k + 1,
                total + t,
                current,
                best,
                best_total,
                nodes,
                trail,
                scores,
                rest,
                conflicts,
            );
            current.pop();
        }
    }

    /// The lazily ordered search visits the same nodes in the same order as the fully sorted one:
    /// the same assignment, total and node count, whether it finishes or stops at its bound, on
    /// random groups with ties, empty inventories and many conflicts.
    #[test]
    fn the_lazy_search_matches_the_sorted_one() {
        let mut seed = 5u64;
        let mut stopped = 0;
        for round in 0..24 {
            let m = 3 + round % 10;
            let scores: Vec<Vec<i64>> = (0..m)
                .map(|k| {
                    let n = if k == 1 && round % 4 == 0 {
                        0
                    } else {
                        1 + (lcg(&mut seed) * KEEP as f64) as usize
                    };
                    (0..n)
                        .map(|_| {
                            (lcg(&mut seed) * 4.0) as i64 * CROSSING / 2
                                + (lcg(&mut seed) * 6.0) as i64
                        })
                        .collect()
                })
                .collect();
            let sizes: Vec<usize> = scores.iter().map(Vec::len).collect();
            let density = 0.05 + 0.3 * lcg(&mut seed);
            let mut pairs = BTreeMap::new();
            for a in 0..m {
                for b in a + 1..m {
                    if lcg(&mut seed) < 0.3 {
                        continue;
                    }
                    // Conflicts of one crossing and of two (text touched).
                    let matrix: Vec<Vec<u32>> = (0..sizes[a])
                        .map(|_| {
                            (0..sizes[b])
                                .map(|_| {
                                    let x = lcg(&mut seed);
                                    u32::from(x < density) + u32::from(x < density / 3.0)
                                })
                                .collect()
                        })
                        .collect();
                    if matrix.iter().flatten().any(|x| *x > 0) {
                        pairs.insert((a, b), matrix);
                    }
                }
            }
            let members: Vec<usize> = (0..m).collect();
            let conflicts = Conflicts::new(&members, &sizes, &pairs);
            // A poor incumbent (each job's first candidate), so the search has work to do.
            let start: Vec<usize> = vec![0; m];
            let start_total = i64::MAX / 2;
            let mut search = Search::new(&scores, &conflicts, start.clone(), start_total);
            search.from(0, 0);
            let (mut best, mut best_total, mut nodes, mut trail) = (start, start_total, 0, 0);
            reference_search(
                0,
                0,
                &mut Vec::new(),
                &mut best,
                &mut best_total,
                &mut nodes,
                &mut trail,
                &scores,
                &search.rest,
                &conflicts,
            );
            stopped += usize::from(nodes >= NODE_BUDGET);
            assert_eq!(
                (search.best, search.best_total, search.nodes, search.trail),
                (best, best_total, nodes, trail),
                "round {round}"
            );
        }
        // Both ends: searches that finish and searches stopped at the bound.
        assert!(stopped > 0 && stopped < 24, "{stopped} of 24 stopped");
    }

    #[test]
    fn lines_laid_along_each_other_meet() {
        // A leader up an extension line meets it; one beside it, or end to end, does not.
        assert!(lines_meet(
            [[0.0, 0.0], [0.0, 10.0]],
            [[0.1, 5.0], [0.1, 20.0]]
        ));
        assert!(!lines_meet(
            [[0.0, 0.0], [0.0, 10.0]],
            [[2.0, 5.0], [2.0, 20.0]]
        ));
        assert!(!lines_meet(
            [[0.0, 0.0], [0.0, 10.0]],
            [[0.0, 10.0], [0.0, 20.0]]
        ));
    }

    #[test]
    fn contact_bounds_preserve_crossings_and_parallel_runs() {
        let mut seed = 7143_u64;
        let mut coordinate = || {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
            (seed >> 32) as f64 / u32::MAX as f64 * 200. - 100.
        };
        for _ in 0..10_000 {
            let a = [[coordinate(), coordinate()], [coordinate(), coordinate()]];
            let b = [[coordinate(), coordinate()], [coordinate(), coordinate()]];
            assert_eq!(
                lines_meet(a, b),
                segments_cross(a, b) || segments_along(a, b)
            );
            assert_eq!(Line::new(a).meets(b), lines_meet(a, b));
        }
        for angle in 0..32 {
            let theta = f64::from(angle) * std::f64::consts::TAU / 32.;
            let u = [theta.cos(), theta.sin()];
            let point = |t: f64, offset: f64| [t * u[0] - offset * u[1], t * u[1] + offset * u[0]];
            for offset in [0., 0.999999, 1., 1.000001, 2.] {
                let a = [point(0., 0.), point(10., 0.)];
                let b = [point(5., offset), point(15., offset)];
                assert_eq!(
                    lines_meet(a, b),
                    segments_cross(a, b) || segments_along(a, b)
                );
            }
        }
    }

    #[test]
    fn crossing_is_strict() {
        assert!(segments_cross(
            [[0.0, 0.0], [2.0, 2.0]],
            [[0.0, 2.0], [2.0, 0.0]]
        ));
        assert!(!segments_cross(
            [[0.0, 0.0], [1.0, 1.0]],
            [[1.0, 1.0], [2.0, 0.0]]
        ));
        assert!(!segments_cross(
            [[0.0, 0.0], [1.0, 0.0]],
            [[2.0, 0.0], [3.0, 0.0]]
        ));
    }
}

#[cfg(test)]
mod arc_tip_tests {
    use super::*;

    #[test]
    fn resolved_visibility_survives_analysis_round_trip_and_matches_rendered_curves() {
        use crate::model::{self, Analysis, Projections, Spec};
        use crate::output::{Curve, Item};
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/grm02.step");
        let (part, mut analysis) = model::analyse_native(&path).unwrap();
        // Resolved visibility is optional evidence. Exercise its serialized
        // contract explicitly for all three principal projections.
        let resolved = crate::model::projected_ink::resolved(
            &part,
            &analysis.binding,
            &super::super::NAMES.into_iter().collect(),
        );
        analysis.edge_ink.as_mut().unwrap().resolved = resolved;
        let analysis: Analysis =
            serde_json::from_slice(&serde_json::to_vec(&analysis).unwrap()).unwrap();
        assert_eq!(analysis.edge_ink.as_ref().unwrap().resolved.len(), 3);
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        let geometry =
            model::render_geometry(&part, &analysis, &plan, &mut Projections::default()).unwrap();
        // Reusing recognition's exact projection must produce the same visible
        // and hidden geometry as a cold kernel projection of every camera.
        let mut cold = analysis.clone();
        cold.binding.source_sha256 = "uncached projection reference".into();
        let reference =
            model::render_geometry(&part, &cold, &plan, &mut Projections::default()).unwrap();
        assert_eq!(
            serde_json::to_value(&geometry).unwrap(),
            serde_json::to_value(&reference).unwrap(),
        );
        let mut views = 0;
        let mut hidden = false;
        for view in plan
            .views
            .iter()
            .filter(|v| v.id != "v:iso" && v.cut.is_none() && v.detail.is_none())
        {
            let (index, _) =
                SegmentIndex::for_view(&analysis, analysis.edge_ink.as_ref().unwrap(), view);
            let resolved = index
                .inspection(&analysis, view)
                .expect("principal ink is in the serialized Analysis");
            let drawn = &geometry
                .views
                .iter()
                .find(|(id, _)| id == &view.id)
                .unwrap()
                .1;
            hidden |= !drawn.hidden.is_empty();
            let b = Frame::new(&analysis, view).outline();
            for i in 0..19 {
                let y = |f: f64| b[1] + (i as f64 + f) / 19. * (b[3] - b[1]);
                let line = [[b[0] - 3., y(0.37)], [b[2] + 3., y(0.79)]];
                for (expected, actual) in [
                    (&drawn.visible, &resolved.visible),
                    (&drawn.hidden, &resolved.hidden),
                ] {
                    // Independent dense sampling of the renderer's actual curves,
                    // rather than the router's adaptive sampler or coarse B-rep edges.
                    let crossings: usize = expected
                        .iter()
                        .map(|item| match item {
                            Item::Edge(c) => {
                                let n = if matches!(c, Curve::Line(..)) {
                                    1
                                } else {
                                    4096
                                };
                                (0..n)
                                    .filter(|i| {
                                        segments_cross(
                                            line,
                                            [
                                                c.at(*i as f64 / n as f64),
                                                c.at((*i + 1) as f64 / n as f64),
                                            ],
                                        )
                                    })
                                    .count()
                            }
                            _ => 0,
                        })
                        .sum();
                    assert_eq!(actual.crossings(line), crossings, "{} query {i}", view.id);
                }
            }
            views += 1;
        }
        assert!(views >= 2 && hidden);
    }

    #[test]
    fn exported_hidden_ink_counts_rounded_endpoint_crossings() {
        // FTC-10: the analytic leader meets the hidden edge's endpoint.
        // Rounding the SVG endpoint to six places leaves a strict crossing.
        let route = [
            [[311.05331089993444, 300.0213243599738], [323.5, 305.]],
            [[323.5, 305.], [324.25, 305.]],
        ];
        let edge = [[314.902, 303.], [318.5, 303.]];
        assert!(!segments_cross(route[0], edge));
        let mut ink = vec![edge];
        ink.extend((0..40).map(|i| [[i as f64, 0.], [i as f64, 10.]]));
        let exported = SegmentIndex::exported(ink);
        assert_eq!(exported.route_crossings(route), 1);
        assert_eq!(exported.route_crossings(route), 1);
    }

    #[test]
    fn physical_edge_identity_survives_curve_sampling_but_rejects_another_edge() {
        let curve = vec![
            [[0., -1.], [0., 1.]],
            [[0., 1.], [1., 1.]],
            [[1., 1.], [1., -1.]],
        ];
        let other = [[2., -1.], [2., 1.]];
        let mut all = curve.clone();
        all.push(other);
        let mut index = SegmentIndex::new(all);
        index.physical = vec![PhysicalEdge::new(curve), PhysicalEdge::new(vec![other])];
        let old = [[[-0.5, 0.], [0.5, 0.]], [[0.5, 0.], [0.5, 0.5]]];
        let same = [[[0.5, 0.], [1.5, 0.]], [[1.5, 0.], [1.5, 0.5]]];
        let different = [[[1.5, 0.], [2.5, 0.]], [[2.5, 0.], [2.5, 0.5]]];
        assert!(index.new_crossing(&same, &old));
        assert!(!index.new_physical_crossing(&same, &old));
        assert!(index.new_physical_crossing(&different, &old));
    }

    #[test]
    fn a_radius_tip_is_constrained_to_its_trimmed_span() {
        use std::f64::consts::PI;
        let site = (
            [0.0, 0.0],
            8.5,
            Some(crate::arc::Span {
                start: PI,
                sweep: PI / 2.0,
            }),
        );
        let tip = rim_tip(site, [-20.0, 20.0]);
        assert!((tip[0] + 8.5).abs() < 1e-9 && tip[1].abs() < 1e-9);
        let tip = rim_tip(site, [-20.0, -20.0]);
        assert!((tip[0] + 8.5 / 2.0_f64.sqrt()).abs() < 1e-9);
        assert!((tip[1] + 8.5 / 2.0_f64.sqrt()).abs() < 1e-9);
        // Growing a nominal can move the shoulder outside the arc's angular
        // range. The repair must retain the drawn quarter rather than attach
        // to the unused portion of its construction circle.
        let repaired = moved_rim_tip(
            &[site],
            tip,
            [-20.0, 20.0],
            crate::model::projected_ink::GRID,
        );
        assert!((repaired[0] + 8.5).abs() < 1e-9 && repaired[1].abs() < 1e-9);
        // A retained proven point need not be a representative site of the
        // complete group. Moving its shoulder cannot jump to another occurrence.
        let retained = [-8.5, 0.0];
        assert_eq!(
            moved_rim_tip(
                &[([4.0, 4.0], 0.0, None)],
                retained,
                [-20.0, 5.0],
                crate::model::projected_ink::GRID
            ),
            retained
        );
        // A full-circle hole retains its unconstrained radial attachment.
        let hole = rim_tip(([0.0, 0.0], 8.5, None), [-20.0, 20.0]);
        assert!(hole[1] > 0.0);
    }
}
