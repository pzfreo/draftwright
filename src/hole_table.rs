//! Drop and escalate (the maintainer's decision of 2026-10-09: draftwright's
//! `_prepare_hole_table_run`): a hole callout or hole location the placement could not put on the
//! sheet is dropped and reported, and the drop escalates to a plan-view hole table with balloons.
//!
//! The port's placement keeps every item and reports what collides, so "could not be placed" is
//! read from the placed plan: a hole callout (its bore diameter, of holes or a hole pattern) or a
//! hole location (`location.length.*` of holes or hole patterns only) that placement reports
//! `annotation_off_sheet` has no place on the sheet. Its ink is withdrawn (it stays in the plan,
//! unplaced, as an item no shown view carries does) and a problem says so, with draftwright's
//! code: `callout_dropped`, `location_ref_dropped` for a location in the plan view, or
//! `off_axis_location_dropped` (info, as draftwright's) for a location of a hole the plan does
//! not see end-on. Every drop is a required loss the layout recovery reads
//! ([`crate::model::recover`]), as every `*_dropped` is a blocker of draftwright's
//! `_is_required_scale_drop`.
//!
//! Then draftwright's escalation, with its trigger and gates (`orchestrator.py`
//! `_maybe_tabulate_holes_impl`):
//!
//! - It runs only when a callout or a plan-view location was dropped; an off-axis location drop
//!   never tabulates (draftwright's distinct code, `hole_locations.py` `_off_axis_drop`).
//! - **The table** is warranted only for a dense plan view: when the plan view's holes outside
//!   any recognised pattern (each member of each group of identical holes, in the plan's order)
//!   number at least 16 (`_TABULATE_MIN_HOLES`). It then has one row per such hole, tagged A, B,
//!   …, Z, AA, … (`_tag_sequence`), `TAG | ⌀ | DEPTH | X | Y`: the diameter as the hole's callout
//!   states it, its tolerance included; THRU or the depth; the X and Y locations from the part's
//!   box, as the plan's location dimensions state them (blank when none does). It replaces the
//!   plan-view callouts of plain holes (no counterbore, spotface or countersink, no authored
//!   tolerance or fit: `_hole_table_replaceable_feature`) and the plan-view location dimensions
//!   that locate only tabulated holes; a compound callout keeps its leader beside its row. The
//!   table goes towards the sheet's top right clear of the views, the remaining dimensions, the
//!   title block, the notes block and the spec's tables, widened into up to four side-by-side
//!   blocks until it fits (`_wrap_rows`, `_build_table`'s `block_cols`).
//! - **Each row has a balloon**, a ring of 4.5 mm radius with its tag, leadered from the hole's
//!   rim: draftwright's `render_balloons` bands left, right, above and below the plan view, each
//!   beyond the deepest annotation placed there and along the view's reserved area, and its strip
//!   spacing. The table is kept only if every row's balloon lands; otherwise it is withdrawn
//!   whole, every replaced item restored and the drops left reported (`table_dropped`,
//!   `balloon_dropped`), as draftwright rolls its transaction back.
//! - **A dropped pattern callout** in the plan view gets one balloon `n×TAG` on its first member,
//!   tagged after the table's rows. It does not state the pattern's sizes, so its
//!   `callout_dropped` stays (draftwright's non-certifying marker).
//!
//! A table that replaces a dropped item clears its drop: the row states what it would have.
//! Lint never reads the plan: the drawing records each row as an annotation claiming the row's
//! hole's bore diameter (and depth) and the locations its cells state, with the cells' text as
//! its own ([`crate::model::render`]), so hole coverage credits the rows as it credits a callout,
//! as draftwright registers the table's measurements and requirements.
//!
//! Departures from draftwright, each with its verdict (`docs/rules`, `tests/hole_tables.rs`):
//! - "Could not be placed" is an item placed off the sheet. draftwright also drops an item that
//!   collides or whose ladder is illegibly crowded; the port places those and reports them
//!   (rust-correct for now: the collisions are another stream's, and an illegible ladder is read
//!   by the recovery from the placed plan).
//! - No balloon halo is reserved by the layout. Band assignment uses min-cost max-flow,
//!   preserving cardinality and certifying row keys before minimising distance. Members spread
//!   in geometric order. A blocked ring or leader is retried on every band; candidates also
//!   reject dimension and extension line crossings. Routing failure reports balloon_dropped
//!   and withdraws the whole automatic table, as before.
//!   Retained feature leaders also reject overlaps and T contacts; a shared endpoint alone
//!   is harmless.
//! - The rows come from the plan's callouts: a hole whose callout the spec omits has no row
//!   (draftwright tabulates every z hole of its model, with blank cells) (undetermined).

use std::collections::{BTreeMap, BTreeSet};

use haecceity::hlr::View;
use serde::{Deserialize, Serialize};

use crate::bridge::holes;
use crate::dims::{Measurement, PlannedDimension};
use crate::layout::{Corner, Rect};
use crate::model::{Analysis, Plan, PlannedView, Problem};
use crate::tables::{self, PlannedTable, ROW};

/// The table's name, draftwright's.
pub const ID: &str = "hole_table_plan";
/// The fewest holes outside a pattern that warrant a table (draftwright's `_TABULATE_MIN_HOLES`).
pub const MIN_HOLES: usize = 16;
/// The table's header.
pub const HEADER: [&str; 5] = ["TAG", "⌀", "DEPTH", "X", "Y"];
/// A balloon's radius (draftwright's `_balloon_radius`: 1.5 text heights) and its ring's stroke.
pub const RADIUS: f64 = 1.5 * crate::dims::TEXT;
pub const RING: f64 = 0.35;
/// The clearance between the view's outline and what is placed beside it, and between strip
/// neighbours (draftwright's `_STRIP_GAP`, `_STRIP_SPACING`).
const STRIP_GAP: f64 = 10.0;
const STRIP_SPACING: f64 = 2.5;
/// The whitespace between side-by-side blocks of a wrapped table (`_table_metrics`' `3 * pad`).
pub(crate) const BLOCK_GAP: f64 = 3.0 * tables::PAD;
/// How many half strip steps a balloon is tried either way of its hole along a band.
const STEPS: usize = 40;

/// The drop codes: each a required loss to the layout recovery.
pub const DROPS: [&str; 3] = [
    "callout_dropped",
    "location_ref_dropped",
    "off_axis_location_dropped",
];

/// The plan-view hole table: one row per hole, drawn as blocks side by side.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HoleTable {
    #[serde(
        default = "crate::sheets::one",
        skip_serializing_if = "crate::sheets::is_one"
    )]
    pub sheet: u32,
    pub id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub origin: Option<[f64; 3]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pcd: Option<PitchCircle>,
    /// Original scalar measurements conveyed by the member ordinates, for item coverage.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub covered: Vec<Measurement>,
    /// One row per hole, in the plan's order.
    pub rows: Vec<HoleRow>,
    pub default: bool,
    pub basis: String,
    /// Always shown: a table withdrawn is not in the plan.
    pub state: crate::items::State,
    /// The blocks drawn, left to right: each the header and its share of the rows (the last
    /// padded with blank rows), placed.
    pub blocks: Vec<PlannedTable>,
}

/// A concentric circle located from the same datum, with explicit member angles.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PitchCircle {
    pub centre: [f64; 3],
    pub diameter: f64,
    pub members: Vec<String>,
}

/// A hole's row: its tag, its hole, the cells it prints and the measurements they state.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HoleRow {
    pub tag: String,
    pub feature: String,
    /// `TAG`, `⌀`, `DEPTH`, `X`, `Y`.
    pub cells: Vec<String>,
    pub measurements: Vec<Measurement>,
}

/// A balloon: a ring with its tag, leadered from a hole's rim.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Balloon {
    /// `balloon_plan_<tag>_0`, draftwright's name.
    pub id: String,
    pub tag: String,
    pub view: String,
    /// The holes it marks: one hole (a table row's), or a pattern's members.
    pub features: Vec<String>,
    /// Whether a table row states what it marks (a pattern's balloon states nothing).
    pub certifying: bool,
    /// Interior face attachments use a dot; outline attachments use an arrow.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub interior: bool,
    /// The ring's centre, mm on the page.
    pub at: [f64; 2],
    pub radius: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub label_box: Option<Rect>,
    /// The leader from the hole's rim (its arrowhead's tip) to the ring; none when the ring
    /// could not clear the hole.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub leader: Option<[[f64; 2]; 2]>,
    /// Always shown: a balloon with no place is not in the plan.
    pub state: crate::items::State,
}

/// What a dropped item was.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Dropped {
    /// A callout; of a recognised pattern or not.
    Callout { pattern: bool },
    /// A location; in the plan view or another.
    Location { plan: bool },
}

/// A hole callout or hole location, by what it measures.
fn hole_item(d: &PlannedDimension) -> Option<Dropped> {
    let hole = |f: &str| f.starts_with("f:hole:") || f.starts_with("f:hole_pattern:");
    let ms: Vec<_> = d.measurements.iter().collect();
    if d.kind == "callout"
        && ms.iter().any(|m| m.parameter == "bore.diameter")
        && ms.iter().all(|m| hole(&m.feature))
    {
        return Some(Dropped::Callout {
            pattern: d.id.starts_with("d:f:hole_pattern:"),
        });
    }
    if d.kind == "linear"
        && !ms.is_empty()
        && ms
            .iter()
            .all(|m| m.parameter.starts_with("location.length.") && hole(&m.feature))
    {
        return Some(Dropped::Location {
            plan: d.view == "v:plan",
        });
    }
    None
}

/// draftwright's tags for *n* rows: `A, B, …, Z, AA, AB, …` (`_tag_sequence`).
pub fn tags(n: usize) -> Vec<String> {
    (0..n)
        .map(|i| {
            let mut s = Vec::new();
            let mut k = i as i64;
            loop {
                s.push(char::from(b'A' + (k % 26) as u8));
                k = k / 26 - 1;
                if k < 0 {
                    break;
                }
            }
            s.iter().rev().collect()
        })
        .collect()
}

/// The bore features *d* calls out, in its order.
fn bores(d: &PlannedDimension) -> Vec<&str> {
    d.measurements
        .iter()
        .filter(|m| m.parameter == "bore.diameter")
        .map(|m| m.feature.as_str())
        .collect()
}

/// A plan with a problem for *d*'s drop, its ink withdrawn and the placement problems about it
/// gone with it.
fn drop_item(plan: &mut Plan, i: usize, kind: Dropped) {
    let d = &mut plan.dimensions[i];
    d.placed = None;
    let id = d.id.clone();
    let (code, severity, what) = match kind {
        Dropped::Callout { .. } => ("callout_dropped", "warning", "hole callout"),
        Dropped::Location { plan: true } => ("location_ref_dropped", "warning", "hole location"),
        Dropped::Location { plan: false } => ("off_axis_location_dropped", "info", "hole location"),
    };
    let off_axis = if kind == (Dropped::Location { plan: false }) {
        "; a hole not seen in the plan has no hole-table row (draftwright's off-axis drop)"
    } else {
        ""
    };
    d.state = crate::items::State::not_drawable(code);
    let message = format!(
        "The {what} {} is dropped from {}: placed, its ink ran off the sheet's drawing area, so it is not drawn{off_axis}. A larger sheet or a smaller scale may give it room.",
        d.label,
        crate::wording::view(&d.view)
    );
    let mut subjects = vec![id.clone()];
    for m in &d.measurements {
        if !subjects.contains(&m.feature) {
            subjects.push(m.feature.clone());
        }
    }
    plan.problems
        .retain(|p| !(p.code.starts_with("annotation_") && p.subjects.contains(&id)));
    plan.problems.push(Problem {
        id: format!("p:{code}:{id}"),
        code: code.into(),
        severity: severity.into(),
        message,
        subjects,
        ..Default::default()
    });
}

/// A view's world-to-page map and its outline on the page.
struct Frame {
    broken: Option<crate::broken::Break>,
    view: View,
    at: [f64; 2],
    s: f64,
    centre: [f64; 2],
    outline: Rect,
}

impl Frame {
    fn new(analysis: &Analysis, v: &PlannedView) -> Frame {
        // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
        let view = View::new(v.direction, v.up).expect("a planned view");
        let (size, centre) = crate::detail::crop_extent(analysis, v);
        let (at, s) = (v.at, v.scale);
        Frame {
            broken: v.broken.clone(),
            view,
            at,
            s,
            centre,
            outline: [
                at[0] - 0.5 * s * size[0],
                at[1] - 0.5 * s * size[1],
                at[0] + 0.5 * s * size[0],
                at[1] + 0.5 * s * size[1],
            ],
        }
    }

    fn page(&self, p: [f64; 3]) -> [f64; 2] {
        let q = self.view.map(p);
        let q = self.broken.as_ref().map_or(q, |b| b.map(q));
        [
            self.at[0] + self.s * (q[0] - self.centre[0]),
            self.at[1] + self.s * (q[1] - self.centre[1]),
        ]
    }
}

/// Why rescue escalation did or did not retain its table. Only NoPlace permits allocation.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum TableOutcome {
    Kept,
    NoPlace,
    Balloons,
    #[default]
    None,
}

/// Drops what has no place and escalates as draftwright does (the module's doc). Applied to
/// every plan the recovery judges.
pub fn escalate(analysis: &Analysis, plan: &mut Plan, table_sheet: Option<u32>) -> TableOutcome {
    // A full-part rescue must not introduce coordinate owners beside an authored network.
    if !plan.spec.dimension_schemes.is_empty() {
        return TableOutcome::None;
    }
    let mut outcome = TableOutcome::None;
    let restore = table_sheet
        .filter(|n| *n > 1)
        .map(|_| Box::new(plan.clone()));
    if plan.spec.hole_table == Setting::Always
        && plan
            .problems
            .iter()
            .any(|p| p.code == "hole_table_unavailable")
    {
        return TableOutcome::None;
    }

    let off: BTreeSet<String> = plan
        .problems
        .iter()
        .filter(|p| p.code == "annotation_off_sheet" && p.severity != "info")
        .filter_map(|p| p.subjects.first().cloned())
        .collect();
    let drops: Vec<(usize, Dropped)> = plan
        .dimensions
        .iter()
        .enumerate()
        .filter(|(_, d)| d.placed.is_some() && off.contains(&d.id))
        .filter(|(_, d)| {
            !plan
                .spec
                .dimensions
                .get(&d.id)
                .and_then(|e| e.place.as_ref())
                .is_some_and(|p| p.pinned)
        })
        .filter_map(|(i, d)| Some((i, hole_item(d)?)))
        .collect();
    for &(i, kind) in &drops {
        drop_item(plan, i, kind);
    }
    let escalating: Vec<usize> = drops
        .iter()
        .filter(|(_, k)| !matches!(k, Dropped::Location { plan: false }))
        .map(|(i, _)| *i)
        .collect();
    if escalating.is_empty()
        || plan.spec.hole_table == Setting::Never
        || crate::dims::pmi_policy::strict(analysis, &plan.spec)
        || !plan.hole_tables.is_empty()
    {
        return TableOutcome::None;
    }
    let Some(view) = plan.views.iter().find(|v| v.id == "v:plan").cloned() else {
        return TableOutcome::None;
    };
    let frame = Frame::new(analysis, &view);
    let sites: BTreeMap<&str, ([f64; 3], f64)> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| {
            let h = h.ok()?;
            Some((f.id.as_str(), (h.at, h.diameter)))
        })
        .collect();
    let hole_site = |f: &str| {
        sites
            .get(f)
            .map(|(at, d)| (frame.page(*at), 0.5 * d * frame.s))
    };

    // The dropped pattern callouts of the plan view, each a balloon of its own.
    let patterns: Vec<usize> = drops
        .iter()
        .filter(|(i, k)| {
            *k == Dropped::Callout { pattern: true } && plan.dimensions[*i].view == "v:plan"
        })
        .map(|(i, _)| *i)
        .collect();
    // The plan view's holes outside any pattern: each member of each group's callout.
    let loose = loose(analysis, plan);
    let tabulate = loose.len() >= MIN_HOLES;
    if !tabulate && patterns.is_empty() {
        let why = format!(
            " No hole table: the plan view has {} hole{} outside a pattern, fewer than the {MIN_HOLES} that warrant one (draftwright's), and no pattern's callout was dropped there.",
            loose.len(),
            if loose.len() == 1 { "" } else { "s" }
        );
        for &i in &escalating {
            say(plan, i, &why);
        }
        return TableOutcome::None;
    }
    let n_rows = if tabulate { loose.len() } else { 0 };
    let all_tags = tags(n_rows + patterns.len());
    let (row_tags, pattern_tags) = all_tags.split_at(n_rows);

    let mut attempt = plan.dimensions.clone();
    let mut table = None;
    let mut replaced: Vec<usize> = Vec::new();
    let mut table_failure = None;
    if tabulate {
        let (rows, replace) = rows(analysis, plan, &loose, row_tags);
        if replace.iter().any(|&i| {
            plan.spec
                .dimensions
                .get(&plan.dimensions[i].id)
                .and_then(|e| e.place.as_ref())
                .is_some_and(|p| p.pinned)
        }) {
            for &(i, _) in &drops {
                say(
                    plan,
                    i,
                    " No hole table: it would replace a pinned hole item.",
                );
            }
            return TableOutcome::None;
        }
        for &i in &replace {
            attempt[i].placed = None;
            attempt[i].state = crate::items::State::Covered { by: ID.into() };
        }
        match table_sheet
            .filter(|n| *n > 1)
            .map(|_| Vec::new())
            .or_else(|| place_table(analysis, plan, &attempt, &rows))
        {
            Some(blocks) => {
                table = Some(HoleTable {
                    sheet: table_sheet.filter(|n| *n > 1).unwrap_or(1),
                    id: ID.into(),
                    view: None,
                    origin: None,
                    pcd: None,
                    covered: Vec::new(),
                    rows,
                    default: true,
                    state: crate::items::State::Shown,
                    basis: format!(
                        "draftwright's drop and escalate: a hole callout or location had no place on the sheet, and the plan view has {} holes outside a pattern (at least {MIN_HOLES}), so each is a row, tagged and ballooned, and the table replaces the plan view's plain callouts and the locations of its holes; towards the sheet's top right in {} block{}",
                        loose.len(),
                        blocks.len(),
                        if blocks.len() == 1 { "" } else { "s" }
                    ),
                    blocks,
                });
                replaced = replace;
            }
            None => {
                outcome = TableOutcome::NoPlace;
                attempt = plan.dimensions.clone();
                table_failure = Some(
                    "it has no place on the sheet clear of the views, the dimensions, the title block, the notes and the spec's tables, even widened into four blocks"
                        .to_string(),
                );
            }
        }
    }

    // The balloons: the rows' first (they decide the table), then the patterns'.
    let mut specs: Vec<Wanted> = Vec::new();
    if let Some(t) = &table {
        for r in &t.rows {
            if let Some((hole, radius)) = hole_site(&r.feature) {
                specs.push(Wanted {
                    tag: r.tag.clone(),
                    features: vec![r.feature.clone()],
                    hole,
                    radius,
                    certifying: true,
                });
            }
        }
    }
    for (&i, tag) in patterns.iter().zip(pattern_tags) {
        let d = &plan.dimensions[i];
        let members: Vec<String> = bores(d).into_iter().map(String::from).collect();
        if let Some((hole, radius)) = members.first().and_then(|f| hole_site(f)) {
            specs.push(Wanted {
                tag: format!("{}×{tag}", members.len()),
                features: members,
                hole,
                radius,
                certifying: false,
            });
        }
    }
    let fixed = fixed_obstacles(analysis, plan, &view, table.as_ref());
    let mut landed = place_balloons(&specs, &frame, plan, &attempt, &fixed, table.is_some());
    let rows_landed = table.as_ref().is_none_or(|t| {
        t.rows.len() == specs.iter().filter(|s| s.certifying).count()
            && landed
                .iter()
                .filter(|b| b.as_ref().is_some_and(|b| b.certifying))
                .count()
                == t.rows.len()
    });
    let mut failed_rows = Vec::new();
    if !rows_landed {
        outcome = TableOutcome::Balloons;
        // The table without every row's balloon is withdrawn whole.
        table = None;
        replaced.clear();
        attempt = plan.dimensions.clone();
        let short: Vec<&str> = specs
            .iter()
            .zip(&landed)
            .filter(|(s, b)| s.certifying && b.is_none())
            .map(|(s, _)| s.tag.as_str())
            .collect();
        table_failure = Some(format!(
            "the balloons of {} of its rows ({}) have no place beside the plan view whose ring clears the other rings and whose leader crosses no label, other view, title block, table or other leader",
            short.len(),
            short.join(", ")
        ));
        failed_rows.extend(
            specs
                .iter()
                .zip(&landed)
                .filter(|(s, b)| s.certifying && b.is_none())
                .map(|(s, _)| s.clone()),
        );
        specs.retain(|s| !s.certifying);
        let fixed = fixed_obstacles(analysis, plan, &view, None);
        landed = place_balloons(&specs, &frame, plan, &attempt, &fixed, false);
    }

    // Commit.
    if let Some(why) = table_failure {
        plan.problems.push(Problem {
            id: format!("p:table_dropped:{ID}"),
            code: "table_dropped".into(),
            severity: "warning".into(),
            message: format!(
                "The hole table ({} rows) that would replace the dropped hole items is not drawn: {why}. The drops stay reported.",
                loose.len()
            ),
            // The drops it would have stated; the table itself is not in the plan.
            subjects: escalating
                .iter()
                .map(|&i| plan.dimensions[i].id.clone())
                .collect(),
            refs: vec![ID.into()],
            ..Default::default()
        });
    }
    let unplaced: Vec<&Wanted> = specs
        .iter()
        .zip(&landed)
        .filter(|(_, b)| b.is_none())
        .map(|(s, _)| s)
        .chain(&failed_rows)
        .collect();
    if !unplaced.is_empty() {
        let names: Vec<String> = unplaced.iter().map(|s| s.tag.clone()).collect();
        plan.problems.push(Problem {
            id: "p:balloon_dropped:v:plan".into(),
            code: "balloon_dropped".into(),
            severity: "warning".into(),
            message: format!(
                "{} balloon{} ({}) could not be placed beside the plan view clear of its labels, the other views and the title block.",
                names.len(),
                if names.len() == 1 { "" } else { "s" },
                names.join(", ")
            ),
            subjects: unplaced.iter().flat_map(|s| s.features.clone()).collect(),
            ..Default::default()
        });
    }
    plan.balloons = landed.into_iter().flatten().collect();
    if let Some(t) = table {
        outcome = TableOutcome::Kept;
        let resolved: BTreeSet<&str> = replaced
            .iter()
            .map(|&i| plan.dimensions[i].id.as_str())
            .collect::<BTreeSet<_>>();
        let resolved: BTreeSet<String> = resolved.into_iter().map(String::from).collect();
        for &i in &replaced {
            let d = &mut attempt[i];
            d.basis += &format!(
                "; stated instead by the hole table's rows ({ID}), which replaces the plan view's plain callouts and hole locations"
            );
        }
        plan.dimensions = attempt;
        let cleared: Vec<String> = plan
            .problems
            .iter()
            .filter(|p| {
                matches!(p.code.as_str(), "callout_dropped" | "location_ref_dropped")
                    && p.subjects.first().is_some_and(|s| resolved.contains(s))
            })
            .map(|p| {
                plan.dimensions
                    .iter()
                    .find(|d| d.id == p.subjects[0])
                    .map_or_else(|| "hole item".into(), |d| d.label.clone())
            })
            .collect();
        plan.problems.retain(|p| {
            !(matches!(p.code.as_str(), "callout_dropped" | "location_ref_dropped")
                && p.subjects.first().is_some_and(|s| resolved.contains(s)))
        });
        plan.problems.push(Problem {
            id: format!("p:hole_table_escalated:{ID}"),
            code: "hole_table_escalated".into(),
            severity: "info".into(),
            message: format!(
                "Hole items had no place on the sheet, so the plan view's {} holes outside a pattern are tabulated and ballooned; the table replaces {} callouts and locations{}.",
                t.rows.len(),
                resolved.len(),
                if cleared.is_empty() {
                    String::new()
                } else {
                    format!(" and states the dropped {}", cleared.join(", "))
                }
            ),
            subjects: std::iter::once(ID.to_string())
                .chain(resolved.iter().cloned())
                .collect(),
            ..Default::default()
        });
        plan.hole_table = Some(t);
        plan.table_restore = restore;
    }
    let marked: Vec<(usize, String)> = plan
        .balloons
        .iter()
        .filter(|b| !b.certifying)
        .filter_map(|b| {
            let i = patterns.iter().copied().find(|&i| {
                bores(&plan.dimensions[i]).first().copied()
                    == b.features.first().map(String::as_str)
            })?;
            Some((i, b.tag.clone()))
        })
        .collect();
    for (i, tag) in marked {
        let why = format!(
            " Balloon {tag} marks the pattern beside the plan view; it does not state the pattern's sizes, so the callout stays missing."
        );
        say(plan, i, &why);
    }
    outcome
}

/// A whole rescue table is withdrawn if even its own sheet cannot hold its rows. Restore
/// the original annotation geometry and let the ordinary escalation report its drops.
pub(crate) fn withdraw_no_place(analysis: &Analysis, plan: &mut Plan) {
    let Some(original) = plan.table_restore.take() else {
        return;
    };
    plan.dimensions = original.dimensions;
    plan.balloons = original.balloons;
    plan.hole_table = original.hole_table;
    plan.problems = original.problems;
    plan.table_outcome = escalate(analysis, plan, None);
    if let Some(p) = plan.problems.iter_mut().find(|p| p.code == "table_dropped") {
        p.message = "The hole table is not drawn: it has no place even on a sheet of its own. The drops stay reported.".into();
    }
}

/// The plan view's holes outside any recognised pattern, each member of each group's callout in
/// the plan's order, with its callout's index. A hole inclined to z keeps its callout: the table
/// states a drilling direction along z and a location in the plan, which it has neither of.
fn loose<'a>(analysis: &Analysis, plan: &'a Plan) -> Vec<(usize, &'a str)> {
    let along_z: BTreeSet<&str> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| (h.ok()?.principal?.0 == 2).then_some(f.id.as_str()))
        .collect();
    plan.dimensions
        .iter()
        .enumerate()
        .filter(|(_, d)| d.kind == "callout" && d.view == "v:plan" && d.id.starts_with("d:f:hole:"))
        .filter(|(_, d)| bores(d).iter().all(|f| along_z.contains(f)))
        .flat_map(|(i, d)| bores(d).into_iter().map(move |f| (i, f)))
        .collect()
}

/// The rows a hole table of *plan*'s plan view has, whether or not its escalation runs: one per
/// hole outside a pattern, tagged in order.
pub fn table_rows(analysis: &Analysis, plan: &Plan) -> Vec<HoleRow> {
    let loose = loose(analysis, plan);
    rows(analysis, plan, &loose, &tags(loose.len())).0
}

/// Adds *why* to the drop problem of dimension *i*.
fn say(plan: &mut Plan, i: usize, why: &str) {
    let id = plan.dimensions[i].id.clone();
    if let Some(p) = plan
        .problems
        .iter_mut()
        .find(|p| DROPS.contains(&p.code.as_str()) && p.subjects.first() == Some(&id))
    {
        p.message += why;
    }
}

/// The table's rows for *loose* (each a callout's index and a hole), tagged *tags*, and the
/// dimensions the table replaces.
fn rows(
    analysis: &Analysis,
    plan: &Plan,
    loose: &[(usize, &str)],
    tags: &[String],
) -> (Vec<HoleRow>, Vec<usize>) {
    let read: BTreeMap<&str, holes::Hole> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect();
    let plain = |f: &str| {
        read.get(f).is_some_and(|h| {
            h.counterbore.is_none() && h.spotface.is_none() && h.countersink.is_none()
        })
    };
    let tabulated: BTreeSet<&str> = loose.iter().map(|(_, f)| *f).collect();
    let locations: Vec<(usize, &PlannedDimension)> = plan
        .dimensions
        .iter()
        .enumerate()
        .filter(|(_, d)| {
            d.kind == "linear"
                && d.view == "v:plan"
                && !d.measurements.is_empty()
                && d.measurements.iter().all(|m| {
                    matches!(
                        m.parameter.as_str(),
                        "location.length.x" | "location.length.y"
                    )
                })
        })
        .collect();
    let origin = crate::dims::pmi_policy::origin_point(analysis, &plan.spec);
    let location = |f: &str, axis: &str| {
        let parameter = format!("location.length.{axis}");
        let found = locations.iter().find_map(|(_, d)| {
            d.measurements
                .iter()
                .find(|m| m.feature == f && m.parameter == parameter)
                .map(|m| (d.label.clone(), m.clone()))
        })?;
        // A location from a round feature's axis (#223) is no coordinate from the origin: the
        // row states the hole's own.
        let k = usize::from(axis == "y");
        match read.get(f).map(|h| h.at[k] - origin[k]) {
            Some(distance) if (found.1.value - distance.abs()).abs() > 1e-6 => Some((
                crate::dims::fmt(distance),
                Measurement {
                    value: distance,
                    ..found.1
                },
            )),
            _ => Some(found),
        }
    };
    let mut out = Vec::new();
    for ((i, f), tag) in loose.iter().zip(tags) {
        let d = &plan.dimensions[*i];
        let at = d.terms.iter().position(|t| t.starts_with('⌀'));
        let tolerance = !d.from_spec.is_empty();
        let diameter = at.map_or(String::new(), |k| {
            let mut s = format!("ø{}", &d.terms[k]['⌀'.len_utf8()..]);
            if tolerance
                && let Some(t) = d
                    .terms
                    .get(k + 1)
                    .filter(|t| !matches!(t.as_str(), "THRU" | "↧"))
            {
                s += &format!(" {t}");
            }
            s
        });
        let depth = if d.terms.iter().any(|t| t == "THRU") {
            "THRU".to_string()
        } else {
            d.terms
                .iter()
                .position(|t| t == "↧")
                .and_then(|k| {
                    d.terms
                        .iter()
                        .skip(k + 1)
                        .find(|t| !crate::callouts::is_break(t))
                })
                .cloned()
                .unwrap_or_default()
        };
        let mut measurements: Vec<Measurement> = d
            .measurements
            .iter()
            .filter(|m| {
                m.feature == *f && matches!(m.parameter.as_str(), "bore.diameter" | "bore.depth")
            })
            .cloned()
            .collect();
        let mut cells = vec![tag.clone(), diameter, depth];
        for axis in ["x", "y"] {
            match location(f, axis) {
                Some((label, m)) => {
                    cells.push(label);
                    measurements.push(m);
                }
                None => cells.push(String::new()),
            }
        }
        out.push(HoleRow {
            tag: tag.clone(),
            feature: (*f).into(),
            cells,
            measurements,
        });
    }
    let mut replace: Vec<usize> = Vec::new();
    for (i, d) in plan.dimensions.iter().enumerate() {
        if d.kind != "callout" || d.view != "v:plan" || !d.id.starts_with("d:f:hole:") {
            continue;
        }
        let members = bores(d);
        if !members.is_empty()
            && members.iter().all(|f| tabulated.contains(f) && plain(f))
            && d.from_spec.is_empty()
            && d.measurements
                .iter()
                .all(|m| matches!(m.parameter.as_str(), "bore.diameter" | "bore.depth"))
        {
            replace.push(i);
        }
    }
    for (i, d) in &locations {
        if !crate::label_text::overridden(d)
            && d.measurements
                .iter()
                .all(|m| tabulated.contains(m.feature.as_str()))
        {
            replace.push(*i);
        }
    }
    replace.sort();
    (out, replace)
}

/// The obstacles every block keeps clear of: the views' areas, *dims*' ink and the PMI's, the
/// title block, the notes block and the spec's tables.
fn table_obstacles(analysis: &Analysis, plan: &Plan, dims: &[PlannedDimension]) -> Vec<Rect> {
    let mut ink: Vec<PlannedDimension> = dims.to_vec();
    ink.extend(plan.gdt.iter().filter_map(crate::dims::gdt::proxy));
    let mut out = tables::obstacles(analysis, &plan.sheet, &plan.views, &ink);
    out.extend(plan.notes_block.as_ref().map(|b| b.cells));
    out.extend(plan.radius_notes_block.as_ref().map(|b| b.cells));
    out.extend(
        plan.tables
            .iter()
            .chain(plan.radius_table.iter().map(|t| &t.table))
            .filter(|t| t.sheet == plan.sheet.number)
            .filter_map(|t| t.placed.as_ref().map(|p| p.cells)),
    );
    out
}

/// The table's blocks, placed towards the sheet's top right, in the fewest blocks (up to four)
/// that fit; none when no wrapping fits.
fn place_table(
    analysis: &Analysis,
    plan: &Plan,
    dims: &[PlannedDimension],
    rows: &[HoleRow],
) -> Option<Vec<PlannedTable>> {
    let obstacles = table_obstacles(analysis, plan, dims);
    place_rows(rows, &plan.sheet, &obstacles, columns(&plan.spec, ID))
}

/// The same four-block wrapping on a sheet without view/annotation obstacles.
pub fn place_on_sheet(
    table: &HoleTable,
    sheet: &crate::model::PlannedSheet,
    spec: &crate::model::Spec,
) -> Option<Vec<PlannedTable>> {
    place_rows(&table.rows, sheet, &[sheet.title_block], columns(spec, ID))
}

/// The tables `sheet/table_columns` may split, by ID.
pub const SPLIT_TABLES: [&str; 4] = [
    ID,
    "hole_schedule_front",
    "hole_schedule_plan",
    "hole_schedule_side",
];
/// The most side-by-side blocks a hole table is split into.
pub const MAX_COLUMNS: usize = 4;

/// The author's block count for table *id* (`sheet/table_columns`), if set.
pub(crate) fn columns(spec: &crate::model::Spec, id: &str) -> Option<usize> {
    spec.sheet.table_columns.get(id).map(|&n| n as usize)
}

/// A table of *headers* and *body* rows split into *n* blocks side by side, as common
/// drawing practice splits a long table: each block repeats the header rows, and the body
/// rows keep their order, reading down each block and then across; the last block is padded
/// with blank rows so the blocks are one height.
pub fn split(headers: &[Vec<String>], body: &[Vec<String>], n: usize) -> Vec<Vec<Vec<String>>> {
    let width = headers.first().or(body.first()).map_or(0, Vec::len);
    let per = body.len().div_ceil(n.max(1));
    (0..n.max(1))
        .map(|c| {
            let mut block = headers.to_vec();
            block.extend((0..per).map(|r| {
                body.get(c * per + r)
                    .cloned()
                    .unwrap_or_else(|| vec![String::new(); width])
            }));
            block
        })
        .collect()
}

/// The block counts to try for a table of *headers* and *body* rows, in order: the author's
/// count alone; otherwise from the fewest whose height fits *page*'s drawable height (one when
/// none does) up to [`MAX_COLUMNS`], so a table that fits is never split for its height.
fn counts(
    fixed: Option<usize>,
    headers: usize,
    body: usize,
    page: [f64; 2],
) -> std::ops::RangeInclusive<usize> {
    if let Some(n) = fixed {
        return n..=n;
    }
    let height = page[1] - 2.0 * crate::layout::margin();
    let fewest = (1..=MAX_COLUMNS)
        .find(|&n| ROW * (headers + body.div_ceil(n)) as f64 <= height)
        .unwrap_or(1);
    fewest..=MAX_COLUMNS
}

/// Each block's column widths and the whole split's size: *n* blocks of *widths*-wide
/// columns (one width set for every block), [`BLOCK_GAP`] apart.
fn split_size(widths: &[f64], rows: usize, n: usize) -> [f64; 2] {
    [
        n as f64 * widths.iter().sum::<f64>() + BLOCK_GAP * (n - 1) as f64,
        ROW * rows as f64,
    ]
}

/// The sizes a hole table may take, one per block count it may be split into, fewest blocks
/// first (only the author's count when `sheet/table_columns` sets one): what the sheet
/// choice reserves for it ([`crate::sheet_policy`]).
pub(crate) fn split_sizes(t: &HoleTable, spec: &crate::model::Spec) -> Vec<[f64; 2]> {
    let counts = match columns(spec, &t.id) {
        Some(n) => n..=n,
        None => 1..=MAX_COLUMNS,
    };
    if t.view.is_some() {
        let cells = schedule_cells(t);
        let widths = schedule_widths(&cells);
        counts
            .map(|n| split_size(&widths, 2 + (cells.len() - 2).div_ceil(n), n))
            .collect()
    } else {
        let (header, body) = rescue_cells(&t.rows);
        counts
            .map(|n| {
                let blocks = split(&header, &body, n);
                let w = blocks.iter().flat_map(|b| tables::widths(b)).sum::<f64>()
                    + BLOCK_GAP * (n - 1) as f64;
                [w, ROW * blocks[0].len() as f64]
            })
            .collect()
    }
}

/// The rescue table's header row and body rows.
fn rescue_cells(rows: &[HoleRow]) -> (Vec<Vec<String>>, Vec<Vec<String>>) {
    (
        vec![HEADER.iter().map(|s| s.to_string()).collect()],
        rows.iter().map(|r| r.cells.clone()).collect(),
    )
}

fn place_rows(
    rows: &[HoleRow],
    sheet: &crate::model::PlannedSheet,
    obstacles: &[Rect],
    fixed: Option<usize>,
) -> Option<Vec<PlannedTable>> {
    let (header, body) = rescue_cells(rows);
    for ncols in fixed.map_or(1..=MAX_COLUMNS, |n| n..=n) {
        let blocks = split(&header, &body, ncols);
        let widths: Vec<Vec<f64>> = blocks.iter().map(|b| tables::widths(b)).collect();
        let w = widths.iter().flatten().sum::<f64>() + BLOCK_GAP * (ncols - 1) as f64;
        let h = ROW * blocks[0].len() as f64;
        let Some([x0, y0]) = tables::place([w, h], sheet, obstacles, Corner::TopRight) else {
            continue;
        };
        let mut x = x0;
        let mut out = Vec::new();
        for (k, (b, ws)) in blocks.iter().zip(&widths).enumerate() {
            let placed = tables::lay(b, ws, [x, y0]);
            x = placed.cells[2] + BLOCK_GAP;
            out.push(PlannedTable {
                sheet: sheet.number,
                id: if ncols == 1 {
                    ID.into()
                } else {
                    format!("{ID}/{}", k + 1)
                },
                rows: b.clone(),
                row_claims: Vec::new(),
                default: true,
                basis: format!("block {} of {ncols} of the hole table", k + 1),
                state: crate::items::State::Shown,
                placed: Some(placed),
            });
        }
        return Some(out);
    }
    None
}

/// What a balloon's ring and leader keep clear of besides the labels: the other views' areas,
/// the title block, the notes block, the spec's tables and the hole table.
fn fixed_obstacles(
    analysis: &Analysis,
    plan: &Plan,
    own: &PlannedView,
    table: Option<&HoleTable>,
) -> Vec<Rect> {
    let mut out: Vec<Rect> = plan
        .views
        .iter()
        .filter(|v| v.id != own.id && v.sheet == own.sheet)
        .map(|v| {
            if v.id == "v:iso" {
                let ib = analysis.drawing_iso_bounds();
                [
                    v.at[0] + v.scale * ib[0],
                    v.at[1] + v.scale * ib[1],
                    v.at[0] + v.scale * ib[2],
                    v.at[1] + v.scale * ib[3],
                ]
            } else if table.is_some_and(|t| t.view.is_some()) {
                Frame::new(analysis, v).outline
            } else {
                v.area
            }
        })
        .collect();
    out.push(plan.sheet.title_block);
    out.extend(plan.notes_block.as_ref().map(|b| b.cells));
    out.extend(plan.radius_notes_block.as_ref().map(|b| b.cells));
    out.extend(
        plan.tables
            .iter()
            .chain(plan.radius_table.iter().map(|t| &t.table))
            .filter(|t| t.sheet == plan.sheet.number)
            .filter_map(|t| t.placed.as_ref().map(|p| p.cells)),
    );
    if let Some(t) = table.filter(|t| t.sheet == own.sheet) {
        out.extend(
            t.blocks
                .iter()
                .filter_map(|b| b.placed.as_ref().map(|p| p.cells)),
        );
    }
    out
}

/// A line from one point to another, mm on the page.
type Segment = [[f64; 2]; 2];

/// A balloon wanted: its tag, what it marks, its hole on the page and the hole's radius there.
#[derive(Clone)]
struct Wanted {
    tag: String,
    features: Vec<String>,
    hole: [f64; 2],
    radius: f64,
    certifying: bool,
}

/// One band beside the view: its free axis (0 along x, 1 along y), the fixed coordinate of its
/// line, and the free stretches of that line a ring's centre may take.
struct Band {
    axis: usize,
    line: f64,
    free: Vec<[f64; 2]>,
}

/// Whether boxes *a* and *b* overlap.
fn overlap(a: Rect, b: Rect) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}

/// Whether segment *s* runs through box *b* (Liang–Barsky).
fn crosses(s: [[f64; 2]; 2], b: Rect) -> bool {
    let [p, q] = s;
    let d = [q[0] - p[0], q[1] - p[1]];
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for k in 0..2 {
        for (num, den) in [(p[k] - b[k], -d[k]), (b[k + 2] - p[k], d[k])] {
            if den.abs() < 1e-12 {
                if num < 0.0 {
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
    }
    t0 < t1
}

/// Directions tried around a hole for its tag.
const TAG_STEPS: usize = 32;
/// Rings (1 mm apart) tried for a tag beside its hole, without a leader.
const TAG_NEAR: usize = 4;
/// Rings (1 mm apart) tried for a leadered tag.
const TAG_FAR: usize = 36;
/// Holes within this page distance (mm) set a leadered tag's outward direction.
const TAG_NEIGHBOURS: f64 = 40.0;
/// A leadered tag's cost, in mm of leader, of each model line its leader crosses.
const TAG_CROSSING: f64 = 1.5;
/// A leadered tag's cost, in mm of leader, per radian it turns from outward.
const TAG_TURN: f64 = 2.0;
/// Orders tried for a view's leadered tags before its schedule is given up.
const TAG_ATTEMPTS: usize = 12;

/// Page segments bucketed in square cells, for clearance queries near one place.
struct Grid {
    segments: Vec<Segment>,
    cells: BTreeMap<(i64, i64), Vec<usize>>,
    /// The query each segment was last judged in, so a query judges it once.
    seen: std::cell::RefCell<(u32, Vec<u32>)>,
}

impl Grid {
    const CELL: f64 = 5.0;

    fn key(x: f64, y: f64) -> (i64, i64) {
        (
            (x / Self::CELL).floor() as i64,
            (y / Self::CELL).floor() as i64,
        )
    }

    fn new(segments: impl Iterator<Item = Segment>) -> Grid {
        let segments: Vec<Segment> = segments.collect();
        let mut cells: BTreeMap<(i64, i64), Vec<usize>> = BTreeMap::new();
        for (i, s) in segments.iter().enumerate() {
            let lo = Self::key(s[0][0].min(s[1][0]), s[0][1].min(s[1][1]));
            let hi = Self::key(s[0][0].max(s[1][0]), s[0][1].max(s[1][1]));
            for x in lo.0..=hi.0 {
                for y in lo.1..=hi.1 {
                    cells.entry((x, y)).or_default().push(i);
                }
            }
        }
        let seen = std::cell::RefCell::new((0, vec![0; segments.len()]));
        Grid {
            segments,
            cells,
            seen,
        }
    }

    /// Whether *ok* holds for every segment whose cells meet box *b* (each judged once,
    /// stopping at the first that fails).
    fn all_clear(&self, b: Rect, mut ok: impl FnMut(Segment) -> bool) -> bool {
        let (lo, hi) = (Self::key(b[0], b[1]), Self::key(b[2], b[3]));
        let mut seen = self.seen.borrow_mut();
        let (query, last) = &mut *seen;
        *query += 1;
        for x in lo.0..=hi.0 {
            for y in lo.1..=hi.1 {
                for &i in self.cells.get(&(x, y)).into_iter().flatten() {
                    if last[i] == *query {
                        continue;
                    }
                    last[i] = *query;
                    if !ok(self.segments[i]) {
                        return false;
                    }
                }
            }
        }
        true
    }
}

/// Whether box *a* lies wholly inside box *b*.
fn within(a: Rect, b: Rect) -> bool {
    a[0] >= b[0] && a[1] >= b[1] && a[2] <= b[2] && a[3] <= b[3]
}

/// The gap between box *b* and the rim of radius *r* about *c* (negative inside the rim).
fn rim_gap(b: Rect, c: [f64; 2], r: f64) -> f64 {
    let dx = (b[0] - c[0]).max(c[0] - b[2]).max(0.0);
    let dy = (b[1] - c[1]).max(c[1] - b[3]).max(0.0);
    dx.hypot(dy) - r
}

/// Whether an unleadered tag at *at* (box *b*) plainly names the hole at *hole*: its box is
/// nearer that rim than any other scheduled rim, and its centre markedly nearer that centre.
fn names_own(
    b: Rect,
    at: [f64; 2],
    hole: [f64; 2],
    radius: f64,
    others: &[([f64; 2], f64)],
) -> bool {
    let own_gap = rim_gap(b, hole, radius);
    let own = (at[0] - hole[0]).hypot(at[1] - hole[1]);
    others.iter().all(|&(c, r)| {
        rim_gap(b, c, r) > own_gap + 1.0 && (at[0] - c[0]).hypot(at[1] - c[1]) > own * 1.3
    })
}

/// A tag's leader: from the hole's rim (the arrow's tip) toward the tag's centre, ending at
/// the tag box's side. None when the box reaches the rim.
fn tag_leader(hole: [f64; 2], radius: f64, at: [f64; 2], b: Rect) -> Option<Segment> {
    let d = [at[0] - hole[0], at[1] - hole[1]];
    let len = d[0].hypot(d[1]);
    if len < 1e-9 {
        return None;
    }
    let u = [d[0] / len, d[1] / len];
    let tip = [hole[0] + u[0] * radius, hole[1] + u[1] * radius];
    // Where the ray from the centre leaves the box, walking back from its centre.
    let half = [(b[2] - b[0]) * 0.5, (b[3] - b[1]) * 0.5];
    let t = [
        half[0] / u[0].abs().max(1e-12),
        half[1] / u[1].abs().max(1e-12),
    ]
    .into_iter()
    .fold(f64::INFINITY, f64::min);
    let end = [at[0] - u[0] * (t + 0.4), at[1] - u[1] * (t + 0.4)];
    let run = (end[0] - tip[0]) * u[0] + (end[1] - tip[1]) * u[1];
    (run > crate::dims::ARROW + 0.5).then_some([tip, end])
}

/// Whether a tag's leader stays off other labels, other tags' leaders and every other
/// scheduled hole's rim. Like any leader it may cross model lines and extension lines.
fn leader_clear(
    l: Segment,
    labels: &[Rect],
    leaders: &[Segment],
    others: &[([f64; 2], f64)],
) -> bool {
    labels.iter().all(|b| !crosses(l, *b))
        && leaders.iter().all(|m| !segments_cross(l, *m))
        && others.iter().all(|&(c, r)| point_segment(c, l) > r + 0.6)
}

/// The distance from *p* to segment *s*.
fn point_segment(p: [f64; 2], s: Segment) -> f64 {
    let [a, b] = s;
    let d = [b[0] - a[0], b[1] - a[1]];
    let len2 = d[0] * d[0] + d[1] * d[1];
    let t = if len2 < 1e-18 {
        0.0
    } else {
        (((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / len2).clamp(0.0, 1.0)
    };
    (p[0] - a[0] - t * d[0]).hypot(p[1] - a[1] - t * d[1])
}

/// The free stretches of [lo, hi] once each of *blocked* is taken out.
fn carve(lo: f64, hi: f64, mut blocked: Vec<[f64; 2]>) -> Vec<[f64; 2]> {
    blocked.sort_by(|a, b| a[0].total_cmp(&b[0]));
    let mut out = Vec::new();
    let mut at = lo;
    for [a, b] in blocked {
        if a > at {
            out.push([at, a.min(hi)]);
        }
        at = at.max(b);
        if at >= hi {
            break;
        }
    }
    if at < hi {
        out.push([at, hi]);
    }
    out.retain(|s| s[1] >= s[0]);
    out
}

/// Positions near *naturals* (ascending), at least *gap* apart, within [lo, hi], nearest in the
/// least-squares sense (isotonic regression, clipped to the bounds).
fn spread(naturals: &[f64], gap: f64, lo: f64, hi: f64) -> Vec<f64> {
    let n = naturals.len();
    // Pool adjacent violators over the naturals less their rank's gaps: blocks of (mean, count).
    let mut blocks: Vec<(f64, usize)> = Vec::new();
    for (i, v) in naturals.iter().enumerate() {
        blocks.push((v - i as f64 * gap, 1));
        while blocks.len() > 1 && blocks[blocks.len() - 2].0 > blocks[blocks.len() - 1].0 {
            // Invariant: The while condition requires at least two blocks before the two pops.
            let (m2, c2) = blocks.pop().expect("a block");
            // Invariant: The while condition requires at least two blocks before the two pops.
            let (m1, c1) = blocks.pop().expect("a block");
            let c = c1 + c2;
            blocks.push(((m1 * c1 as f64 + m2 * c2 as f64) / c as f64, c));
        }
    }
    let top = (hi - n.saturating_sub(1) as f64 * gap).max(lo);
    blocks
        .iter()
        .flat_map(|&(m, c)| std::iter::repeat_n(m.clamp(lo, top), c))
        .enumerate()
        .map(|(i, y)| y + i as f64 * gap)
        .collect()
}

/// Each wanted balloon placed beside the plan view, or none: draftwright's bands left, right,
/// above and below the view, each just beyond the deepest annotation placed there, the members
/// spread along them at the strip spacing and kept off what is placed (the module's doc).
/// Maximum-cardinality, minimum-cost assignment to capacitated bands. Residual reverse
/// edges let a later constrained member displace an earlier flexible one (Python ADR0014).
fn assign_bands(
    costs: &[Vec<Option<f64>>],
    capacities: &[usize],
    preference: f64,
) -> Vec<Option<usize>> {
    #[derive(Clone)]
    struct Edge {
        to: usize,
        rev: usize,
        cap: usize,
        cost: f64,
    }
    fn add(g: &mut [Vec<Edge>], a: usize, b: usize, cap: usize, cost: f64) {
        let (ar, br) = (g[a].len(), g[b].len());
        g[a].push(Edge {
            to: b,
            rev: br,
            cap,
            cost,
        });
        g[b].push(Edge {
            to: a,
            rev: ar,
            cap: 0,
            cost: -cost,
        });
    }
    let bands = 1 + costs.len();
    let sink = bands + capacities.len();
    let mut graph = vec![Vec::<Edge>::new(); sink + 1];
    for (i, row) in costs.iter().enumerate() {
        add(&mut graph, 0, i + 1, 1, 0.);
        for (k, cost) in row.iter().enumerate() {
            if let Some(c) = cost {
                add(&mut graph, i + 1, bands + k, 1, *c);
            }
        }
    }
    for (k, &capacity) in capacities.iter().enumerate() {
        if capacity > 0 && preference > 0. {
            add(&mut graph, bands + k, sink, 1, -preference);
            add(&mut graph, bands + k, sink, capacity - 1, 0.);
        } else {
            add(&mut graph, bands + k, sink, capacity, 0.);
        }
    }
    loop {
        let mut dist = vec![f64::INFINITY; sink + 1];
        let mut prev = vec![None; sink + 1];
        let mut queue = std::collections::VecDeque::from([0]);
        let mut queued = vec![false; sink + 1];
        dist[0] = 0.;
        queued[0] = true;
        while let Some(a) = queue.pop_front() {
            queued[a] = false;
            for (j, e) in graph[a].iter().enumerate().filter(|(_, e)| e.cap > 0) {
                let d = dist[a] + e.cost;
                if d < dist[e.to] - 1e-9 {
                    dist[e.to] = d;
                    prev[e.to] = Some((a, j));
                    if !queued[e.to] {
                        queue.push_back(e.to);
                        queued[e.to] = true;
                    }
                }
            }
        }
        if prev[sink].is_none() {
            break;
        }
        let mut b = sink;
        while b != 0 {
            // Invariant: prev was filled while reaching b before the augmentation starts.
            let (a, j) = prev[b].expect("augmenting path");
            let rev = graph[a][j].rev;
            graph[a][j].cap -= 1;
            graph[b][rev].cap += 1;
            b = a;
        }
    }
    (0..costs.len())
        .map(|i| {
            graph[i + 1]
                .iter()
                .find(|e| e.to >= bands && e.to < sink && e.cap == 0)
                .map(|e| e.to - bands)
        })
        .collect()
}

/// Place assembly references using the same capacitated bands and crossing predicates as
/// hole-table tags. Targets are already proven visible by the assembly reader's face sources.
pub(crate) fn item_references(
    targets: &[(String, [f64; 2])],
    analysis: &Analysis,
    view: &PlannedView,
    radius: f64,
    plan: &Plan,
    fixed: &[Rect],
) -> Vec<Option<Balloon>> {
    let wanted: Vec<_> = targets
        .iter()
        .map(|(tag, hole)| Wanted {
            tag: tag.clone(),
            features: Vec::new(),
            hole: *hole,
            radius: 0.0,
            certifying: false,
        })
        .collect();
    place_reference_bands(
        &wanted,
        Frame::new(analysis, view).outline,
        &view.id,
        radius,
        plan,
        &plan.dimensions,
        fixed,
        false,
    )
}

fn place_balloons(
    wanted: &[Wanted],
    frame: &Frame,
    plan: &Plan,
    dims: &[PlannedDimension],
    fixed: &[Rect],
    perimeter: bool,
) -> Vec<Option<Balloon>> {
    place_reference_bands(
        wanted,
        frame.outline,
        "v:plan",
        RADIUS,
        plan,
        dims,
        fixed,
        perimeter,
    )
}

/// Shared item-reference bands. Hole tags retain their original frame and radius.
#[allow(clippy::too_many_arguments)]
fn place_reference_bands(
    wanted: &[Wanted],
    outline: Rect,
    view: &str,
    r: f64,
    plan: &Plan,
    dims: &[PlannedDimension],
    fixed: &[Rect],
    perimeter: bool,
) -> Vec<Option<Balloon>> {
    if wanted.is_empty() {
        return Vec::new();
    }
    let [pw, ph] = plan.sheet.page;
    let m = crate::layout::FRAME;
    let drawable = [m, m, pw - m, ph - m];
    // The halo draftwright reserves for a table's ring, else the nearest standoff.
    let extent = if perimeter {
        STRIP_GAP + 2.0 * r + STRIP_SPACING
    } else {
        STRIP_GAP + 2.0 * r
    };
    let offset = extent - r;
    let mut ink: Vec<PlannedDimension> = dims.to_vec();
    ink.extend(plan.gdt.iter().filter_map(crate::dims::gdt::proxy));
    // These feature leaders survive the replacement. Their actual segments, rather than
    // their compound bounding rectangles, are required ink the optional balloons must clear.
    let retained: Vec<Segment> = ink
        .iter()
        .filter(|d| d.view == view && matches!(d.kind.as_str(), "callout" | "diameter" | "radius"))
        .filter_map(|d| d.placed.as_ref())
        .flat_map(|p| p.lines.iter().copied())
        .collect();
    let mut labels: Vec<Rect> = Vec::new();
    let mut occupants: Vec<Rect> = Vec::new();
    for p in ink.iter().filter_map(|d| d.placed.as_ref()) {
        labels.push(p.label_box);
        occupants.push(p.label_box);
        for l in p.lines.iter().chain(&p.arrows) {
            occupants.push([
                l[0][0].min(l[1][0]),
                l[0][1].min(l[1][1]),
                l[0][0].max(l[1][0]),
                l[0][1].max(l[1][1]),
            ]);
        }
    }
    let [pl, pb, pr, pt] = outline;
    // How deep the placed annotations in the view's reserved area reach into each band
    // (draftwright measures each band up to its strip's limit).
    let area = plan
        .views
        .iter()
        .find(|v| v.id == view)
        .map_or(drawable, |v| v.area);
    let (mut left, mut right, mut top, mut bottom) = (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    for b in occupants.iter().filter(|b| overlap(**b, area)) {
        if b[2] > pl && b[0] < pr {
            if b[3] > pt {
                top = top.max(b[3] - pt);
            }
            if b[1] < pb {
                bottom = bottom.max(pb - b[1]);
            }
        }
        if b[3] > pb && b[1] < pt {
            if b[0] < pl {
                left = left.max(pl - b[0]);
            }
            if b[2] > pr {
                right = right.max(b[2] - pr);
            }
        }
    }
    let glyph_blockers: Vec<Rect> = occupants
        .iter()
        .chain(fixed)
        .copied()
        .chain(std::iter::once(outline))
        .collect();
    let pad = r + STRIP_SPACING;
    let band = |axis: usize, line: f64| -> Band {
        let cross = 1 - axis;
        let fits = line - r >= drawable[cross] && line + r <= drawable[cross + 2];
        // Along the view's reserved area, so a ring stays beside its own view.
        let (lo, hi) = (
            drawable[axis].max(area[axis]) + r,
            drawable[axis + 2].min(area[axis + 2]) - r,
        );
        let blocked = glyph_blockers
            .iter()
            .filter(|b| b[cross] < line + pad && b[cross + 2] > line - pad)
            .map(|b| [b[axis] - pad, b[axis + 2] + pad])
            .collect();
        Band {
            axis,
            line,
            free: if fits {
                carve(lo, hi, blocked)
            } else {
                Vec::new()
            },
        }
    };
    let bands = [
        band(1, pl - left - offset),
        band(1, pr + right + offset),
        band(0, pt + top + offset),
        band(0, pb - bottom - offset),
    ];
    let font = crate::dims::font();
    let gap = wanted
        .iter()
        .map(|w| {
            let laid = font.baseline(&w.tag, crate::dims::TEXT);
            (laid.max[0] - laid.min[0]).max(2.0 * r)
        })
        .fold(2.0 * r, f64::max)
        + 2.0 * STRIP_SPACING;
    // A balloon's place on band *b* at *along*: its centre, and its leader from the hole's rim.
    let at_on = |w: &Wanted, b: &Band, along: f64| {
        let at = if b.axis == 1 {
            [b.line, along]
        } else {
            [along, b.line]
        };
        let d = [at[0] - w.hole[0], at[1] - w.hole[1]];
        let dist = d[0].hypot(d[1]);
        let leader = (dist > w.radius + r).then(|| {
            let u = [d[0] / dist, d[1] / dist];
            [
                [w.hole[0] + u[0] * w.radius, w.hole[1] + u[1] * w.radius],
                [at[0] - u[0] * r, at[1] - u[1] * r],
            ]
        });
        (at, dist, leader)
    };
    let mut rings: Vec<Rect> = Vec::new();
    let mut leaders: Vec<[[f64; 2]; 2]> = Vec::new();
    // Whether a ring at *at* with *leader* clears the labels, the fixed obstacles and the balloons
    // placed so far.
    let clear =
        |at: [f64; 2], leader: Option<[[f64; 2]; 2]>, rings: &[Rect], leaders: &[[[f64; 2]; 2]]| {
            let ring = [at[0] - r, at[1] - r, at[0] + r, at[1] + r];
            let spaced = |q: &Rect| {
                overlap(
                    [
                        q[0] - STRIP_SPACING,
                        q[1] - STRIP_SPACING,
                        q[2] + STRIP_SPACING,
                        q[3] + STRIP_SPACING,
                    ],
                    ring,
                )
            };
            !rings.iter().any(spaced)
                && !retained.iter().any(|s| crosses(*s, ring))
                && leader.is_none_or(|l| {
                    !labels
                        .iter()
                        .chain(fixed)
                        .chain(rings)
                        .any(|q| crosses(l, *q))
                        && !leaders
                            .iter()
                            .chain(&retained)
                            .any(|m| unsafe_contact(l, *m))
                        && !ink
                            .iter()
                            .filter_map(|d| d.placed.as_ref())
                            .flat_map(|p| p.lines.iter())
                            .any(|m| segments_cross(l, *m))
                })
        };
    // Assign capacitated bands jointly, then spread members in geometric order along each
    // free stretch. Remote bands stay empty when a nearer band has capacity.
    let usable: Vec<usize> = (0..bands.len())
        .filter(|&k| !bands[k].free.is_empty())
        .collect();
    let capacities: Vec<_> = bands
        .iter()
        .map(|b| {
            b.free
                .iter()
                .map(|s| ((s[1] - s[0]) / gap).floor() as usize + 1)
                .sum()
        })
        .collect();
    let max_cost = bands
        .iter()
        .flat_map(|b| {
            wanted
                .iter()
                .map(move |w| (w.hole[1 - b.axis] - b.line).abs())
        })
        .fold(0.0f64, f64::max);
    let preference = if perimeter { extent } else { 0. };
    let optional_penalty = (max_cost + preference + 1.) * (wanted.len() + 1) as f64;
    let costs: Vec<Vec<Option<f64>>> = wanted
        .iter()
        .map(|w| {
            bands
                .iter()
                .map(|b| {
                    (!b.free.is_empty()).then_some(
                        (w.hole[1 - b.axis] - b.line).abs()
                            + if w.certifying { 0. } else { optional_penalty },
                    )
                })
                .collect()
        })
        .collect();
    let assignment = assign_bands(&costs, &capacities, preference);
    let mut planned: Vec<Option<(usize, f64)>> = vec![None; wanted.len()];
    for (k, b) in bands.iter().enumerate() {
        let mut members: Vec<usize> = (0..wanted.len())
            .filter(|&i| assignment[i] == Some(k))
            .collect();
        let axis = b.axis;
        members.sort_by(|&x, &y| {
            wanted[x].hole[axis]
                .total_cmp(&wanted[y].hole[axis])
                .then(x.cmp(&y))
        });
        let mut by_stretch: Vec<Vec<usize>> = vec![Vec::new(); b.free.len()];
        let mut members = members.into_iter();
        for (stretch, ids) in b.free.iter().zip(&mut by_stretch) {
            let capacity = ((stretch[1] - stretch[0]) / gap).floor() as usize + 1;
            ids.extend(members.by_ref().take(capacity));
        }
        for (stretch, ids) in b.free.iter().zip(&by_stretch) {
            let naturals: Vec<f64> = ids.iter().map(|&i| wanted[i].hole[axis]).collect();
            for (&i, c) in ids
                .iter()
                .zip(spread(&naturals, gap, stretch[0], stretch[1]))
            {
                planned[i] = Some((k, c));
            }
        }
    }
    let mut out: Vec<Option<Balloon>> = vec![None; wanted.len()];
    let mut place = |i: usize,
                     at: [f64; 2],
                     leader: Option<[[f64; 2]; 2]>,
                     rings: &mut Vec<Rect>,
                     leaders: &mut Vec<[[f64; 2]; 2]>| {
        let w = &wanted[i];
        rings.push([at[0] - r, at[1] - r, at[0] + r, at[1] + r]);
        leaders.extend(leader);
        out[i] = Some(Balloon {
            id: format!("balloon_plan_{}_0", w.tag),
            tag: w.tag.clone(),
            view: view.into(),
            features: w.features.clone(),
            certifying: w.certifying,
            interior: false,
            at,
            radius: r,
            label_box: None,
            leader,
            state: crate::items::State::Shown,
        });
    };
    let mut left_over = Vec::new();
    for (i, p) in planned.iter().enumerate() {
        let fits = p.and_then(|(k, along)| {
            let (at, _, leader) = at_on(&wanted[i], &bands[k], along);
            clear(at, leader, &rings, &leaders).then_some((at, leader))
        });
        match fits {
            Some((at, leader)) => place(i, at, leader, &mut rings, &mut leaders),
            None => left_over.push(i),
        }
    }
    // Then each member left over, nearest a band first, to the clear place on any band nearest its
    // hole: tried at the hole's own coordinate along the band, then half a strip step at a time
    // either way.
    let reach = |w: &Wanted, b: &Band| (w.hole[1 - b.axis] - b.line).abs();
    let nearest = |w: &Wanted| {
        usable
            .iter()
            .map(|&k| reach(w, &bands[k]))
            .fold(f64::INFINITY, f64::min)
    };
    left_over.sort_by(|&a, &b| {
        nearest(&wanted[a])
            .total_cmp(&nearest(&wanted[b]))
            .then(a.cmp(&b))
    });
    let step = 0.5 * gap;
    for i in left_over {
        let w = &wanted[i];
        let mut best: Option<(f64, [f64; 2], Option<Segment>)> = None;
        for &k in &usable {
            let b = &bands[k];
            for s in &b.free {
                for j in 0..=2 * STEPS {
                    let sign = if j % 2 == 1 { 1.0 } else { -1.0 };
                    let along =
                        w.hole[b.axis].clamp(s[0], s[1]) + sign * j.div_ceil(2) as f64 * step;
                    if along < s[0] - 1e-9 || along > s[1] + 1e-9 {
                        continue;
                    }
                    let (at, dist, leader) = at_on(w, b, along);
                    if best.as_ref().is_some_and(|(c, ..)| dist >= *c) {
                        continue;
                    }
                    if clear(at, leader, &rings, &leaders) {
                        best = Some((dist, at, leader));
                    }
                }
            }
        }
        if let Some((_, at, leader)) = best {
            place(i, at, leader, &mut rings, &mut leaders);
        }
    }
    out
}

/// A crossing, T contact or collinear overlap is unsafe; a shared endpoint alone is fine.
fn unsafe_contact(a: Segment, b: Segment) -> bool {
    if segments_cross(a, b) {
        return true;
    }
    let interior = |p: [f64; 2], s: Segment| {
        point_segment(p, s) < 1e-8
            && (p[0] - s[0][0]).hypot(p[1] - s[0][1]) > 1e-8
            && (p[0] - s[1][0]).hypot(p[1] - s[1][1]) > 1e-8
    };
    a.iter().any(|&p| interior(p, b))
        || b.iter().any(|&p| interior(p, a))
        || interior([(a[0][0] + a[1][0]) * 0.5, (a[0][1] + a[1][1]) * 0.5], b)
}

/// Whether segments *a* and *b* cross.
fn segments_cross(a: [[f64; 2]; 2], b: [[f64; 2]; 2]) -> bool {
    let side = |p: [f64; 2], q: [f64; 2], r: [f64; 2]| {
        (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    };
    let (d1, d2) = (side(b[0], b[1], a[0]), side(b[0], b[1], a[1]));
    let (d3, d4) = (side(a[0], a[1], b[0]), side(a[0], a[1], b[1]));
    d1 * d2 < 0.0 && d3 * d4 < 0.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(cells: &[&str]) -> Vec<String> {
        cells.iter().map(|c| c.to_string()).collect()
    }

    #[test]
    fn a_split_repeats_its_headers_and_reads_down_then_across() {
        let headers = vec![row(&["PLAN HOLES", ""]), row(&["TAG", "X"])];
        let body: Vec<_> = (0..5).map(|k| row(&[&k.to_string(), "1"])).collect();
        let blocks = split(&headers, &body, 2);
        assert_eq!(blocks.len(), 2);
        for block in &blocks {
            assert_eq!(&block[..2], &headers[..]);
            assert_eq!(block.len(), 2 + 3);
        }
        let read: Vec<_> = blocks.iter().flat_map(|b| b[2..].to_vec()).collect();
        assert_eq!(&read[..5], &body[..]);
        // The last block is padded with one blank row to the common height.
        assert_eq!(read[5], row(&["", ""]));
        assert_eq!(
            split(&headers, &body, 1),
            vec![[headers.clone(), body].concat()]
        );
    }

    #[test]
    fn only_a_table_taller_than_the_sheet_splits_for_its_height() {
        let a2 = [594.0, 420.0];
        // 2 + 30 rows (224 mm) fit A2's drawable height whole.
        assert_eq!(counts(None, 2, 30, a2), 1..=MAX_COLUMNS);
        // CTC-02's plan schedule: 2 + 61 rows are 441 mm; two blocks of 31 are 231 mm.
        assert_eq!(counts(None, 2, 61, a2), 2..=MAX_COLUMNS);
        // The author's count is the only one tried, even when it does not fit.
        assert_eq!(counts(Some(1), 2, 61, a2), 1..=1);
        assert_eq!(counts(Some(3), 2, 5, a2), 3..=3);
    }

    #[test]
    fn a_schedule_tries_shorter_blocks_only_when_the_whole_table_has_no_clear_place() {
        let sheet = serde_json::from_value(serde_json::json!({
            "size":"A2", "page":[594.0,420.0], "scale":"1:1", "scale_factor":1.0,
            "default":true, "basis":"test", "arrangement":"columns", "state":{"kind":"shown"},
            "title":{}, "title_block":[400.0,13.0,581.0,50.0]
        }))
        .unwrap();
        let table = HoleTable {
            sheet: 1,
            id: "hole_schedule_plan".into(),
            view: Some("v:plan".into()),
            origin: None,
            pcd: None,
            covered: Vec::new(),
            default: true,
            basis: "test".into(),
            state: crate::items::State::Shown,
            blocks: Vec::new(),
            rows: (0..18)
                .map(|i| HoleRow {
                    tag: i.to_string(),
                    feature: format!("f{i}"),
                    measurements: Vec::new(),
                    cells: vec![
                        i.to_string(),
                        "10 THRU".into(),
                        "0".into(),
                        "0".into(),
                        "0 / +Z".into(),
                    ],
                })
                .collect(),
        };
        assert_eq!(reflow_blocks(&table, &sheet, &[], None).unwrap().len(), 1);
        let obstacle = [[0.0, 0.0, 594.0, 325.0]];
        assert!(reflow_blocks(&table, &sheet, &obstacle, Some(1)).is_none());
        let blocks = reflow_blocks(&table, &sheet, &obstacle, None).unwrap();
        assert_eq!(blocks.len(), 2);
        let cells = schedule_cells(&table);
        for block in &blocks {
            assert_eq!(block.rows[..2], cells[..2]);
        }
        assert_eq!(
            blocks
                .iter()
                .flat_map(|b| b.rows[2..].iter().cloned())
                .collect::<Vec<_>>(),
            cells[2..]
        );
        assert_eq!(
            reflow_blocks(&table, &sheet, &obstacle, Some(3))
                .unwrap()
                .len(),
            3
        );
    }

    /// The fallback a schedule installs when replacing PMI callouts would add an error keeps
    /// every PMI size callout beside its row, as before the rows replaced them, and leaves the
    /// PMI claims with those callouts.
    #[test]
    fn the_keeping_selection_leaves_pmi_callouts_beside_their_rows() {
        let (_, a) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/editor_pmi.step"),
        )
        .unwrap();
        let spec = crate::model::Spec {
            hole_table: Setting::Always,
            ..Default::default()
        };
        let mut p = crate::model::plan(&a, &spec).unwrap();
        p.hole_tables.clear();
        let semantic: Vec<String> = p
            .dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.feature.starts_with("pmi:")))
            .filter(|d| !bores(d).is_empty())
            .map(|d| d.id.clone())
            .collect();
        assert!(!semantic.is_empty());
        let claims = |s: &Selection| {
            s.tables
                .iter()
                .flat_map(|t| &t.rows)
                .flat_map(|r| &r.measurements)
                .filter(|m| m.feature.starts_with("pmi:"))
                .count()
        };
        let replacing = select(&a, &p);
        assert!(replacing.pmi_replaced);
        assert!(semantic.iter().all(|id| replacing.replaced.contains(id)));
        assert!(claims(&replacing) > 0);
        let keeping = select_keeping_pmi(&a, &p);
        assert!(!keeping.tables.is_empty() && !keeping.pmi_replaced);
        assert!(semantic.iter().all(|id| !keeping.replaced.contains(id)));
        assert_eq!(claims(&keeping), 0);
    }

    #[test]
    fn detail_obstacle_equals_its_crop_footprint() {
        let (_, a) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/mounting_plate.step"),
        )
        .unwrap();
        let spec = serde_json::from_value(serde_json::json!({"sheet":{"size":"A2","scale":"1:2"},"views":{"detail_c":{"detail":{"around":"f:hole:1","from":"plan","scale_factor":2}}}})).unwrap();
        let p = crate::model::plan(&a, &spec).unwrap();

        let v = p.views.iter().find(|v| v.id == "v:detail_c").unwrap();
        let mut crop_only = p.clone();
        crop_only.views = vec![v.clone()];
        let footprint = crate::sheet_fit::footprints(&a, &crop_only)[0];
        let frame = Frame::new(&a, v);
        assert_eq!(frame.outline, footprint);
        let bounds = v.detail.as_ref().unwrap().bounds;
        let middle = [0, 1, 2].map(|k| (bounds[0][k] + bounds[1][k]) / 2.);
        assert_eq!(frame.page(middle), v.at);
    }

    #[test]
    fn balloon_band_assignment_maximises_cardinality_before_leader_length() {
        // Greedy nearest puts the flexible first hole on the only band the second can use.
        let costs = vec![vec![Some(1.), Some(3.)], vec![Some(2.), None]];
        assert_eq!(assign_bands(&costs, &[1, 1], 0.), vec![Some(1), Some(0)]);
        // The bounded perimeter preference cannot justify a remote band.
        let costs = vec![vec![Some(1.), Some(100.)], vec![Some(2.), Some(100.)]];
        assert_eq!(assign_bands(&costs, &[2, 2], 25.), vec![Some(0), Some(0)]);
        assert_eq!(
            assign_bands(&costs, &[1, 0], 25.).iter().flatten().count(),
            1
        );
        // Python's bounded perimeter preference can justify a nearby band, not a remote one.
        let nearby = vec![vec![Some(1.), Some(20.)]; 3];
        let assigned = assign_bands(&nearby, &[3, 3], 25.);
        assert_eq!(assigned.iter().filter(|&&b| b == Some(0)).count(), 2);
        assert_eq!(assigned.iter().filter(|&&b| b == Some(1)).count(), 1);
    }

    #[test]
    fn retained_shafts_reject_crossings_overlaps_and_t_contacts() {
        assert!(unsafe_contact([[0., 0.], [2., 2.]], [[0., 2.], [2., 0.]]));
        assert!(unsafe_contact([[0., 0.], [3., 0.]], [[1., 0.], [4., 0.]]));
        assert!(unsafe_contact([[0., 0.], [3., 0.]], [[1., -1.], [1., 0.]]));
        assert!(unsafe_contact([[0., 0.], [3., 0.]], [[0., 0.], [3., 0.]]));
        assert!(!unsafe_contact([[0., 0.], [1., 0.]], [[1., 0.], [2., 1.]]));
        assert!(!unsafe_contact([[0., 0.], [1., 0.]], [[0., 1.], [1., 1.]]));
    }

    #[test]
    fn tags_run_as_draftwrights() {
        let t = tags(28);
        assert_eq!(&t[..3], ["A", "B", "C"]);
        assert_eq!(t[25], "Z");
        assert_eq!(&t[26..], ["AA", "AB"]);
        assert_eq!(tags(26 + 26 * 26 + 1).last().unwrap(), "AAA");
    }

    #[test]
    fn spread_keeps_the_gap_and_the_bounds() {
        let s = spread(&[5.0, 5.0, 6.0], 4.0, 0.0, 100.0);
        assert!((s[1] - s[0] - 4.0).abs() < 1e-9 && (s[2] - s[1] - 4.0).abs() < 1e-9);
        assert!((s[1] - 5.333_333_333).abs() < 1e-6);
        assert_eq!(spread(&[0.0, 1.0], 4.0, 10.0, 100.0), [10.0, 14.0]);
        assert_eq!(spread(&[99.0, 100.0], 4.0, 0.0, 100.0), [96.0, 100.0]);
    }

    #[test]
    fn carving_leaves_the_free_stretches() {
        assert_eq!(
            carve(0.0, 10.0, vec![[2.0, 3.0], [-1.0, 1.0], [2.5, 4.0]]),
            [[1.0, 2.0], [4.0, 10.0]]
        );
        assert_eq!(carve(0.0, 10.0, vec![[-1.0, 11.0]]), Vec::<[f64; 2]>::new());
    }

    #[test]
    fn a_segment_crosses_a_box_it_passes_through() {
        let b = [0.0, 0.0, 1.0, 1.0];
        assert!(crosses([[-1.0, 0.5], [2.0, 0.5]], b));
        assert!(!crosses([[-1.0, 2.0], [2.0, 2.0]], b));
        assert!(crosses([[0.5, 0.5], [0.6, 0.6]], b));
        assert!(!crosses([[-1.0, -1.0], [-0.5, 3.0]], b));
    }
}

/// Author control for plan-time schedules and the automatic rescue.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Setting {
    #[default]
    Auto,
    Always,
    Never,
}
impl Setting {
    pub fn is_auto(&self) -> bool {
        *self == Self::Auto
    }
}

/// A bounded presentation decision, made before the final lanes are allocated.
#[derive(Clone, Default)]
pub(crate) struct Selection {
    pub tables: Vec<HoleTable>,
    pub scale: f64,
    pub page: [f64; 2],
    pub iso: Option<PlannedView>,
    pub replaced: BTreeSet<String>,
    /// Whether the dimensions laid out with the schedules keep off the places their origin
    /// marks need ([`origin_reservations`]): set for a second attempt, when a slot callout took
    /// such a place in the first ([`slot_callout_takes_origin`]).
    pub reserve_origins: bool,
    /// Whether a row replaces a callout whose size comes from PMI ([`select`]).
    pub pmi_replaced: bool,
    dimensions: Vec<PlannedDimension>,
    aliases: Vec<crate::dims::Omitted>,
}
impl Selection {
    /// Each schedule's reserved size on *page*: whole, or split into the fewest side-by-side
    /// blocks whose height fits the page (the author's count when `sheet/table_columns` sets
    /// one), as [`install`] first tries to place it.
    pub fn sizes(&self, spec: &crate::model::Spec, page: [f64; 2]) -> Vec<([f64; 2], Corner)> {
        self.tables
            .iter()
            .map(|t| {
                let cells = schedule_cells(t);
                let body = cells.len() - 2;
                let n = *counts(columns(spec, &t.id), 2, body, page).start();
                (
                    split_size(&schedule_widths(&cells), 2 + body.div_ceil(n), n),
                    Corner::TopRight,
                )
            })
            .collect()
    }
}

fn pinned(plan: &Plan, d: &PlannedDimension) -> bool {
    plan.spec
        .dimensions
        .get(&d.id)
        .and_then(|e| e.place.as_ref())
        .is_some_and(|p| p.pinned)
}

fn schedule_number(value: f64) -> String {
    let text = crate::dims::fmt(if value.abs() < 1e-6 { 0.0 } else { value });
    text.strip_suffix(".0").unwrap_or(&text).into()
}

fn has_precision(plan: &Plan, d: &PlannedDimension) -> bool {
    !plan.spec.precision.is_empty()
        && (plan.spec.precision.contains_key(&d.id)
            || d.measurements.iter().any(|m| {
                plan.spec
                    .precision
                    .contains_key(&format!("d:{}/{}", m.feature, m.parameter))
            }))
}

fn coordinate_number(
    plan: &Plan,
    dimension: Option<&PlannedDimension>,
    feature: &str,
    parameter: &str,
    distance: f64,
) -> String {
    if plan.spec.precision.is_empty() {
        return schedule_number(distance);
    }
    let policy = plan
        .spec
        .precision
        .get(&format!("d:{feature}/{parameter}"))
        .or_else(|| {
            dimension.and_then(|d| {
                d.measurements
                    .iter()
                    .filter(|m| m.parameter == parameter)
                    .find_map(|m| {
                        plan.spec
                            .precision
                            .get(&format!("d:{}/{}", m.feature, m.parameter))
                    })
                    .or_else(|| plan.spec.precision.get(&d.id))
            })
        });
    policy.map_or_else(
        || schedule_number(distance),
        |&p| crate::dims::precision::format(distance, p),
    )
}

/// A table carries a view title so its local X/Y cannot be mistaken for another view's.
fn schedule_cells(t: &HoleTable) -> Vec<Vec<String>> {
    let mut title = vec![
        format!(
            "{} HOLES",
            t.view
                .as_deref()
                // Invariant: Schedule tables are constructed for a selected carrying view.
                .unwrap()
                .trim_start_matches("v:")
                .to_uppercase()
        ),
        String::new(),
        String::new(),
        String::new(),
        if t.basis.contains("; origin: ") {
            "ORIGIN"
        } else {
            "FROM MIN"
        }
        .into(),
    ];
    let mut header = vec![
        "TAG".into(),
        "ø / DEPTH / THREAD".into(),
        "X".into(),
        "Y".into(),
        "Z OUT / DIR".into(),
    ];
    if let Some(pcd) = &t.pcd {
        // Invariant: A PCD schedule is constructed with a resolved origin.
        let origin = t.origin.unwrap();
        let axes: Vec<_> = t.rows[0]
            .measurements
            .iter()
            .filter_map(|m| m.parameter.strip_prefix("location.length."))
            .collect();
        let coordinate = |a: &str| {
            // Invariant: Measurement location axes are emitted only as x, y or z.
            let k = ["x", "y", "z"].iter().position(|k| *k == a).unwrap();
            schedule_number(pcd.centre[k] - origin[k])
        };
        title[1] = format!(
            "ø{} PCD @ X{} Y{}",
            schedule_number(pcd.diameter),
            coordinate(axes[0]),
            coordinate(axes[1])
        );
        title.push("CCW FROM +X".into());
        header.push("ANGLE".into());
    }
    let mut cells = vec![title, header];
    cells.extend(t.rows.iter().map(|r| r.cells.clone()));
    cells
}

/// A row's entry cell in its view's frame: X right and Y up as the X and Y columns, Z out of
/// the page toward the reader. The entry plane is measured from the part's rearmost face, as
/// X and Y are from its minimum faces, and the drilling direction is `+Z` (toward the reader)
/// or `−Z` (into the page).
fn entry_cell(analysis: &Analysis, out: [f64; 3], at: [f64; 3], direction: [f64; 3]) -> String {
    let dot = |p: [f64; 3]| p.iter().zip(out).map(|(a, b)| a * b).sum::<f64>();
    let [lo, hi] = analysis.envelope;
    let back = (0..8)
        .map(|i| {
            dot(std::array::from_fn(|k| {
                if (i >> k) & 1 == 0 { lo[k] } else { hi[k] }
            }))
        })
        .fold(f64::INFINITY, f64::min);
    format!(
        "{} / {}Z",
        schedule_number(dot(at) - back),
        if dot(direction) > 0.0 { "+" } else { "−" }
    )
}

/// Size cells use the callout's drawn symbols, which the bundled text font does not contain.
pub(crate) fn size_ink(cell: &str, font: &crate::text::Font) -> crate::dims::LabelInk {
    let terms: Vec<_> = cell
        .split(' ')
        .filter(|t| !t.is_empty())
        .map(String::from)
        .collect();
    crate::dims::label_ink(&terms, font)
}

fn schedule_widths(cells: &[Vec<String>]) -> Vec<f64> {
    let mut widths = tables::widths(cells);
    // Measure size cells with their actual symbols and stacks, not a flat font string.
    widths[1] = tables::widths(&cells[..cells.len().min(2)])[1];
    for row in cells.iter().skip(2) {
        let ink = size_ink(&row[1], crate::dims::font());
        widths[1] = widths[1].max(ink.max[0] - ink.min[0] + 2.0 * tables::PAD);
    }
    widths
}

fn datum_corner(analysis: &Analysis, axes: &[usize]) -> Option<[f64; 3]> {
    // A theoretical corner is the intersection of the minimum datum planes, including
    // tangent planes at a rounded edge. Require the carrying solid's measured boundary.
    analysis
        .principal_profiles
        .iter()
        .any(|s| {
            !s.faces.is_empty()
                && axes
                    .iter()
                    .all(|&k| (s.bounds[0][k] - analysis.envelope[0][k]).abs() < 1e-4)
        })
        .then_some(analysis.envelope[0])
}

/// Use the measured ladder spacing and the existing leader/feature crossing findings. The
/// threshold is in annotation density, never in a part's identity. Bolt circles already have
/// their PCD note and are left intact unless the author explicitly requests a schedule.
pub(crate) fn select(analysis: &Analysis, plan: &Plan) -> Selection {
    select_with(analysis, plan, true)
}

/// [`select`] with every callout whose size comes from PMI kept beside its row: the fallback when
/// replacing those callouts would add an error to the drawing ([`crate::model`]'s schedule
/// installation).
pub(crate) fn select_keeping_pmi(analysis: &Analysis, plan: &Plan) -> Selection {
    select_with(analysis, plan, false)
}

fn select_with(analysis: &Analysis, plan: &Plan, replace_pmi: bool) -> Selection {
    let mut selection = Selection {
        scale: plan.sheet.scale_factor,
        page: plan.sheet.page,
        iso: plan.views.iter().find(|v| v.id == "v:iso").cloned(),
        ..Default::default()
    };
    if plan.spec.hole_table == Setting::Never
        || crate::dims::pmi_policy::strict(analysis, &plan.spec)
        || (plan.spec.hole_table == Setting::Auto
            && plan.spec.dimension_source == crate::model::DimensionSource::Authored)
    {
        return selection;
    }
    let source_locations = crate::dims::pmi_policy::source_locations(analysis, plan);
    // Placement pins keep the canonical (unpinned) plan's sheet and frames (`model::plan_with`);
    // they keep its hole presentation too, so each view is scheduled exactly when the canonical
    // plan schedules it. Re-measuring the trigger on the pinned layout would let a pin's held
    // placement add or remove a whole table. A bare pin on a scalar already covered by the
    // default table does not resurrect its ladder. Callout pins remain additive, so a visible
    // authored callout is never replaced.
    let canonical = if crate::placement_intent::has_pins(&plan.spec)
        && let Some(published) = crate::planning::pin_reference()
    {
        Some(published)
    } else if crate::placement_intent::has_pins(&plan.spec) {
        let mut base = crate::model::Spec::default();
        base.sheet.size = plan.spec.sheet.size.clone();
        base.sheet.scale = plan.spec.sheet.scale.clone();
        base.hole_table = plan.spec.hole_table;
        // A multi-sheet request's canonical plan is its whole R5 allocation: the same
        // sheet permission, with the views this allocation moved after sheet 1.
        if let Some(max) = plan
            .spec
            .sheets
            .as_ref()
            .and_then(|s| s.max)
            .filter(|&n| n > 1)
        {
            base.sheets = Some(crate::sheets::SheetsSpec {
                max: Some(max),
                ..Default::default()
            });
            base.views = crate::sheets::reference_views(&plan.spec);
        }
        crate::model::plan_automatic(analysis, &base).ok()
    } else if plan.spec.hole_table == Setting::Auto
        && crate::dims::has_new_native_recess_dimensions(analysis)
        && plan.gdt.is_empty()
        && plan.dimensions.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        })
    {
        // New recess dimensions must not turn an established hole schedule
        // back into a ladder of locations and displace unrelated callouts.
        // Eligibility, authored/pinned items and installation are still checked.
        crate::model::established_drawing(analysis, &plan.spec).ok()
    } else {
        None
    };
    let covered_pins: BTreeSet<String> = canonical
        .iter()
        .flat_map(|p| &p.omitted)
        .filter(|o| matches!(&o.state, crate::items::State::Covered { by } if by.starts_with("hole_schedule_")))
        .map(|o| o.id.clone())
        .collect();
    let read: BTreeMap<_, _> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect();
    let manufacturing = crate::pmi_lowering::manufacturing(analysis);
    for view in &plan.views {
        if !plan.spec.dimension_schemes.is_empty()
            && plan
                .dimensions
                .iter()
                .any(|d| d.view == view.id && d.id.starts_with("d:scheme:"))
        {
            continue;
        }
        let Some(axis) = view.direction.iter().position(|d| d.abs() > 0.999) else {
            continue;
        };
        if !matches!(view.id.as_str(), "v:front" | "v:plan" | "v:side") {
            continue;
        }
        // An automatic coordinate schedule cannot replace an authored pitch,
        // nor control the same pattern beside that pitch. Keep its ladder;
        // the paper search must find room for the author's printed statement.
        if plan.spec.hole_table == Setting::Auto
            && plan.spec.features.iter().any(|(id, f)| {
                f.pattern.is_some()
                    && plan.dimensions.iter().any(|d| {
                        d.view == view.id
                            && d.measurements.iter().any(|m| {
                                m.feature == *id
                                    && (m.parameter.starts_with("pitch.")
                                        || m.parameter.starts_with("grid_pitch."))
                            })
                    })
            })
        {
            continue;
        }
        let axes: Vec<_> = (0..3).filter(|k| *k != axis).collect();
        let callouts: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| {
                d.view == view.id
                    && d.kind == "callout"
                    && d.measurements
                        .iter()
                        .any(|m| m.parameter == "bore.diameter")
                    && bores(d).iter().all(|f| {
                        read.get(f)
                            .is_some_and(|h| h.principal.is_some_and(|(k, _)| k == axis))
                    })
            })
            .collect();
        let n: usize = callouts.iter().map(|d| bores(d).len()).sum();
        let locations: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| {
                d.view == view.id
                    && !d.measurements.is_empty()
                    && d.measurements.iter().all(|m| {
                        m.feature.starts_with("f:hole")
                            && m.parameter.starts_with("location.length.")
                    })
            })
            .collect();
        let crowded: usize = axes
            .iter()
            .map(|k| {
                let parameter = format!("location.length.{}", ["x", "y", "z"][*k]);
                let mut values: Vec<_> = locations
                    .iter()
                    .filter(|d| d.measurements.iter().any(|m| m.parameter == parameter))
                    .map(|d| d.value)
                    .collect();
                values.sort_by(f64::total_cmp);
                values.dedup_by(|a, b| (*a - *b).abs() < 1e-6);
                crate::layout::crowded_locations(&values, view.scale)
            })
            .sum();
        let conflicts = plan
            .problems
            .iter()
            .filter(|p| {
                matches!(
                    p.code.as_str(),
                    "leader_through_feature" | "annotation_leader_crossing"
                ) && callouts.iter().any(|d| p.subjects.contains(&d.id))
            })
            .count();
        let wanted = match &canonical {
            Some(base) if plan.spec.hole_table == Setting::Auto => base
                .hole_tables
                .iter()
                .any(|t| t.id == format!("hole_schedule_{}", view.id.trim_start_matches("v:"))),
            _ => n >= 6 && (crowded > 0 || locations.len() > 8 || conflicts > 0),
        };
        if plan.spec.hole_table != Setting::Always && !wanted {
            continue;
        }
        // Use the theoretical intersection of the minimum datum planes.
        let Some(mut origin) = datum_corner(analysis, &axes) else {
            continue;
        };
        if crate::dims::pmi_policy::carries(analysis) || !plan.spec.origin.is_empty() {
            origin = crate::dims::pmi_policy::origin_point(analysis, &plan.spec);
        }
        let mut rows = Vec::new();
        let mut replaced = BTreeSet::new();
        let mut frees_native = false;
        let mut pmi_replaced = false;
        for d in &callouts {
            if bores(d).iter().any(|f| {
                axes.iter().any(|k| {
                    let id = format!("d:{f}/location.length.{}", ["x", "y", "z"][*k]);
                    plan.omitted.iter().any(|o| {
                        o.id == id
                            && matches!(
                                o.state,
                                crate::items::State::OmittedByUser
                                    | crate::items::State::NotAuthored
                            )
                    })
                })
            }) {
                continue;
            }
            if plan.spec.hole_table != Setting::Always
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "bolt_circle.diameter")
            {
                continue;
            }
            let terms = d
                .terms
                .iter()
                .filter(|t| !crate::callouts::is_break(t))
                .skip(usize::from(
                    d.terms.first().is_some_and(|t| t.ends_with('×')),
                ))
                .take_while(|t| t.as_str() != "EQ" && !t.starts_with("EQ SP"));
            // Array grammar locates members only in the leader version; the schedule's physical
            // member ordinates replace it. Compound recess/thread wording is retained verbatim.
            // Explicit nominal formatting has already been applied to these terms. Parsing
            // them again would strip its trailing zeroes, including bare recess depths.
            let approved_size = d.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
                || has_precision(plan, d)
                || d.from_spec
                    .iter()
                    .any(|s| matches!(s.as_str(), "fit" | "tolerance"));
            let label = terms
                .filter(|t| {
                    !(t.starts_with('(') && t.contains('×') && t.ends_with(')'))
                        && !t.starts_with("EQ SP")
                })
                .map(|t| {
                    if approved_size {
                        t.clone()
                    } else {
                        t.parse::<f64>().map_or_else(|_| t.clone(), schedule_number)
                    }
                })
                .collect::<Vec<_>>()
                .join(" ");
            // A pin keeps its complete callout. The row is additive for that item. A size from
            // PMI, with its band or fit, is stated by its row exactly as its callout states it,
            // so the row replaces that callout and carries its PMI claims.
            let semantic = d.measurements.iter().any(|m| m.feature.starts_with("pmi:"));
            let authored_size = plan.spec.hole_table == Setting::Auto
                && d.from_spec
                    .iter()
                    .any(|s| matches!(s.as_str(), "fit" | "tolerance"));
            let shared_thread = d.measurements.iter().any(|m| {
                m.parameter == "thread"
                    && manufacturing
                        .owned
                        .iter()
                        .filter(|o| o.id == m.feature)
                        .count()
                        > 1
            });
            let kept = pinned(plan, d)
                || authored_size
                || crate::label_text::overridden(d)
                || (semantic && !replace_pmi && !shared_thread);
            // A schedule repeats every size, including sizes owned by retained PMI.
            let source_callout = d.placed.is_some() && semantic && kept;
            for (member, f) in bores(d).into_iter().enumerate() {
                let h = &read[f];
                let mut measurements: Vec<_> = d
                    .measurements
                    .iter()
                    .filter(|m| m.feature == f)
                    .cloned()
                    .collect();
                if source_callout {
                    measurements.clear();
                } else if member == 0 {
                    // A PMI size names the whole pattern, as its callout did once: every row
                    // prints it, and the first row carries the claim.
                    measurements.extend(
                        d.measurements
                            .iter()
                            .filter(|m| m.feature.starts_with("pmi:"))
                            .cloned(),
                    );
                }
                // The schedule replaces the native drill callout as well as
                // stating the shared thread. Keep that physical size/depth in
                // its own row; M6 alone must not erase the original ø5 bore.
                let mut size = label.clone();
                if shared_thread && !label.contains(['ø', '⌀', 'Ø']) {
                    let mut drill = format!("⌀{}", schedule_number(h.diameter));
                    if h.through {
                        drill += " THRU";
                    } else if let Some(depth) = h.depth {
                        drill += &format!(" ↧ {}", schedule_number(depth));
                    }
                    size = format!("{drill} {size}");
                }
                let mut cells = vec![String::new(), size];
                for &k in &axes {
                    let parameter = format!("location.length.{}", ["x", "y", "z"][k]);
                    let distance = h.at[k] - origin[k];
                    // A location from a round feature's axis (#223) is no coordinate.
                    let measured = plan.dimensions.iter().find(|d| {
                        d.measurements.iter().any(|m| {
                            m.feature == f
                                && m.parameter == parameter
                                && (m.value - distance.abs()).abs() <= 1e-6
                        })
                    });
                    cells.push(
                        measured
                            .filter(|_| (origin[k] - analysis.envelope[0][k]).abs() <= 1e-6)
                            .map_or_else(
                                || coordinate_number(plan, measured, f, &parameter, distance),
                                |d| {
                                    d.terms
                                        .iter()
                                        .skip(usize::from(
                                            d.terms.first().is_some_and(|t| t.ends_with('×')),
                                        ))
                                        .map(|t| {
                                            if has_precision(plan, d) {
                                                t.clone()
                                            } else {
                                                t.parse::<f64>()
                                                    .map_or_else(|_| t.clone(), schedule_number)
                                            }
                                        })
                                        .collect::<Vec<_>>()
                                        .join(" ")
                                },
                            ),
                    );
                    // An already rooted coordinate is a schedule restatement in the
                    // same origin frame, not an additional independent locating control.
                    if !source_locations.contains(&(f.to_string(), k)) {
                        measurements.push(Measurement {
                            feature: f.into(),
                            parameter,
                            value: distance,
                        });
                    }
                }
                cells.push(entry_cell(analysis, view.direction, h.at, h.direction));
                rows.push(HoleRow {
                    tag: String::new(),
                    feature: f.into(),
                    cells,
                    measurements,
                });
            }
            if !kept {
                replaced.insert(d.id.clone());
                // A shared thread newly recognized on several distinct bores must
                // not withdraw their native schedule. The rows retain its complete
                // statement; independent authored sizes keep the existing PMI gate.
                frees_native |= !semantic || shared_thread;
                pmi_replaced |= semantic;
            }
        }
        if rows.is_empty()
            || (plan.spec.hole_table == Setting::Auto
                && crate::dims::pmi_policy::carries(analysis)
                && !frees_native)
        {
            // Whether a PMI part gets a schedule is still decided by the native callouts it
            // frees: a schedule of PMI sizes alone keeps its ordinary inline callouts, and an
            // explicit schedule remains available. Once a schedule exists, its rows replace
            // the PMI callouts too.
            continue;
        }
        let mut pcd: Option<PitchCircle> = None;
        for centre in read
            .values()
            .filter(|h| h.principal.is_some_and(|(k, _)| k == axis))
        {
            for seed in &rows {
                let h = &read[seed.feature.as_str()];
                let radius =
                    (h.at[axes[0]] - centre.at[axes[0]]).hypot(h.at[axes[1]] - centre.at[axes[1]]);
                if radius <= centre.diameter * 0.5 {
                    continue;
                }
                let members: Vec<_> = rows
                    .iter()
                    .filter(|r| {
                        let other = &read[r.feature.as_str()];
                        r.cells[1] == seed.cells[1]
                            && (other.at[axis] - h.at[axis]).abs() < 1e-4
                            && ((other.at[axes[0]] - centre.at[axes[0]])
                                .hypot(other.at[axes[1]] - centre.at[axes[1]])
                                - radius)
                                .abs()
                                < 1e-4
                    })
                    .map(|r| r.feature.clone())
                    .collect();
                let sites: BTreeSet<_> = members
                    .iter()
                    .map(|f| {
                        let h = &read[f.as_str()];
                        (
                            holes::size_key(h.at[axes[0]]),
                            holes::size_key(h.at[axes[1]]),
                        )
                    })
                    .collect();
                if sites.len() >= 5 && pcd.as_ref().is_none_or(|p| members.len() > p.members.len())
                {
                    pcd = Some(PitchCircle {
                        centre: centre.at,
                        diameter: 2.0 * radius,
                        members,
                    });
                }
            }
        }
        if let Some(circle) = &pcd {
            for row in &mut rows {
                let h = &read[row.feature.as_str()];
                row.cells.push(if circle.members.contains(&row.feature) {
                    let angle = (h.at[axes[1]] - circle.centre[axes[1]])
                        .atan2(h.at[axes[0]] - circle.centre[axes[0]])
                        .to_degrees()
                        .rem_euclid(360.0);
                    format!(
                        "{}°",
                        schedule_number(if angle > 360.0 - 1e-6 { 0.0 } else { angle })
                    )
                } else {
                    String::new()
                });
            }
        }
        rows.sort_by(|a, b| {
            a.cells[1]
                .cmp(&b.cells[1])
                .then_with(|| a.feature.cmp(&b.feature))
        });
        let mut group = 0;
        let mut number = 0;
        let mut last = String::new();
        for r in &mut rows {
            if group == 0 || r.cells[1] != last {
                group += 1;
                number = 0;
                last = r.cells[1].clone();
            }
            number += 1;
            r.tag = format!("{}{number}", tags(group)[group - 1]);
            r.cells[0] = r.tag.clone();
        }
        let features: BTreeSet<_> = rows.iter().map(|r| r.feature.as_str()).collect();
        let patterns: BTreeSet<_> = callouts
            .iter()
            .filter(|d| replaced.contains(&d.id))
            .flat_map(|d| d.measurements.iter())
            .filter(|m| m.feature.starts_with("f:hole_pattern:"))
            .map(|m| m.feature.as_str())
            .chain(
                callouts
                    .iter()
                    .filter(|d| replaced.contains(&d.id))
                    .filter_map(|d| d.id.strip_prefix("d:")?.split('/').next())
                    .filter(|f| f.starts_with("f:hole_pattern:")),
            )
            .chain(
                analysis
                    .recognition
                    .iter()
                    .flat_map(|r| &r.patterns)
                    .filter(|p| {
                        crate::dims::pmi_policy::carries(analysis)
                            && p.members.iter().all(|id| features.contains(id.as_str()))
                    })
                    .map(|p| p.id.as_str()),
            )
            .collect();
        for d in &plan.dimensions {
            if d.view == view.id
                && (!pinned(plan, d) || covered_pins.contains(&d.id))
                && !d.from_spec.iter().any(|s| s == "tolerance")
                && !crate::label_text::overridden(d)
                && !d.measurements.is_empty()
                && d.measurements.iter().all(|m| {
                    (features.contains(m.feature.as_str())
                        && m.parameter.starts_with("location.length."))
                        || (patterns.contains(m.feature.as_str())
                            && (m.parameter.starts_with("location.length.")
                                || m.parameter.starts_with("pitch.")
                                || m.parameter.starts_with("grid_pitch.")))
                })
            {
                replaced.insert(d.id.clone());
            }
        }
        selection.dimensions.extend(
            plan.dimensions
                .iter()
                .filter(|d| replaced.contains(&d.id))
                .cloned(),
        );
        selection.replaced.extend(replaced);
        selection.pmi_replaced |= pmi_replaced;
        selection.tables.push(HoleTable {
            sheet: 1,
            id: format!("hole_schedule_{}", view.id.trim_start_matches("v:")), view: Some(view.id.clone()), origin: Some(origin), pcd, covered: Vec::new(),
            rows, default: plan.spec.hole_table == Setting::Auto, basis: {
                let mut basis = format!("plan-time hole schedule: {n} holes, {crowded} crowded ordinates, {conflicts} leader crossings; local X/Y from the marked datum corner");
                if crate::dims::pmi_policy::carries(analysis) || !plan.spec.origin.is_empty() {
                    basis += &format!("; origin: {}", crate::dims::pmi_policy::origin_description(analysis, &plan.spec));
                }
                basis
            }, state: crate::items::State::Shown, blocks: Vec::new(),
        });
    }
    // Consolidated wants otherwise reappear when their table-owned survivor leaves the lanes.
    // Keep the original merged measurement only; its aliases remain covered by that item.
    loop {
        let before = selection.replaced.len();
        for o in &plan.omitted {
            if let crate::items::State::Covered { by } = &o.state
                && selection.replaced.contains(by)
            {
                selection.replaced.insert(o.id.clone());
            }
        }
        if before == selection.replaced.len() {
            break;
        }
    }
    // Coalesced locators may also serve members outside the schedule, so they
    // stay drawn. Do not give their tabulated members a second coordinate.
    let retained = crate::dims::pmi_policy::retained_locations(analysis, plan, &selection.replaced);
    for table in &mut selection.tables {
        let Some(axis) = table
            .view
            .as_ref()
            .and_then(|id| plan.views.iter().find(|v| &v.id == id))
            .and_then(|v| v.direction.iter().position(|d| d.abs() > 0.999))
        else {
            continue;
        };
        let axes: Vec<_> = (0..3).filter(|k| *k != axis).collect();
        for row in &mut table.rows {
            for (column, &k) in axes.iter().enumerate() {
                if let Some(owners) = retained.get(&(row.feature.clone(), k)) {
                    let label = row.cells[2 + column].clone();
                    // Recovery follows the surviving graph owner, even when the
                    // schedule no longer needs a coordinate cell for this member.
                    if let Some(by) = owners.first() {
                        let id =
                            format!("d:{}/location.length.{}", row.feature, ["x", "y", "z"][k]);
                        if !plan
                            .dimensions
                            .iter()
                            .any(|d| d.id == id && d.placed.is_some())
                        {
                            selection.aliases.push(crate::dims::Omitted {
                                id,
                                label,
                                basis: format!(
                                    "located by retained dimension chain: {}",
                                    owners.join(", ")
                                ),
                                state: crate::items::State::Covered { by: by.clone() },
                            });
                        }
                    }
                    row.measurements.retain(|m| {
                        m.parameter != format!("location.length.{}", ["x", "y", "z"][k])
                    });
                }
            }
        }
    }
    selection.aliases.extend(
        plan.omitted
            .iter()
            .filter(|o| selection.replaced.contains(&o.id))
            .cloned(),
    );
    selection
}

/// Place the schedules transactionally. Every row has a nearby tag; none needs a long leader.
/// On failure the caller keeps the original complete plan and the legacy rescue still runs.
/// A schedule taller than the sheet is split into side-by-side blocks ([`counts`]).
pub(crate) fn install(analysis: &Analysis, plan: &mut Plan, selection: &Selection) -> bool {
    if install_at(analysis, plan, selection) {
        return true;
    }
    // Retaining an established isometric scale can outgrow the newly packed
    // reservation. Before spending more paper, retry that automatic orientation
    // view beside the complete schedules at the same scale. Principal views,
    // authored placements and held frames keep their existing geometry.
    if crate::placement_intent::has_pins(&plan.spec)
        || plan
            .spec
            .views
            .get("iso")
            .is_some_and(|v| v.place.is_some())
    {
        return false;
    }
    let Some(iso) = plan
        .views
        .iter()
        .find(|v| v.id == "v:iso" && v.sheet == plan.sheet.number)
    else {
        return false;
    };
    let bounds = analysis.drawing_iso_bounds().map(|b| b * iso.scale);
    if (0..2).all(|k| {
        iso.at[k] + bounds[k] >= iso.area[k] - 1e-6
            && iso.at[k] + bounds[k + 2] <= iso.area[k + 2] + 1e-6
    }) {
        return false;
    }
    let mut retry = plan.clone();
    retry.views.retain(|v| v.id != "v:iso");
    let mut obstacles = table_obstacles(analysis, &retry, &retry.dimensions);
    for (size, corner) in selection.sizes(&retry.spec, retry.sheet.page) {
        let Some([x, y]) = tables::place(size, &retry.sheet, &obstacles, corner) else {
            return false;
        };
        obstacles.push([x, y, x + size[0], y + size[1]]);
    }
    let size = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
    // Keep the caption's normal lower band clear as well as the projected ink.
    let caption = 3.0 * crate::section::FONT_SIZE;
    let target = [size[0], size[1] + caption];
    let margin = crate::layout::FRAME + crate::layout::PREFERRED_BLOCK_GUTTER;
    let region = [
        margin,
        margin,
        plan.sheet.page[0] - margin,
        plan.sheet.page[1] - margin,
    ];
    let Some([x, y]) =
        crate::layout::fit_auxiliary_box(target, region, &obstacles, Corner::TopRight, tables::PAD)
    else {
        return false;
    };
    let mut iso = iso.clone();
    iso.area = [x, y, x + target[0], y + target[1]];
    iso.at = [x - bounds[0], y + caption - bounds[1]];
    iso.basis += "; repacked at the established scale beside the complete hole schedules";
    retry.views.push(iso);
    if !install_at(analysis, &mut retry, selection) {
        return false;
    }
    crate::iso_caption::refresh(analysis, &mut retry);
    *plan = retry;
    true
}

/// A view's model ink on the page, which schedule tags and the origin mark keep clear of: its
/// projected boundary edges with the side-on silhouettes of its bores, and the projected
/// envelopes of curved side faces.
fn model_ink(
    analysis: &Analysis,
    view: &PlannedView,
    frame: &Frame,
    read: &BTreeMap<&str, holes::Hole<'_>>,
) -> (Vec<Segment>, Vec<Rect>) {
    let mut edge_segments: Vec<_> = analysis
        .principal_profiles
        .iter()
        .flat_map(|s| &s.boundary_segments)
        .map(|e| [frame.page(e[0]), frame.page(e[1])])
        .collect();
    // Side-on bores have tangent silhouettes which are not B-rep boundary edges.
    for h in read.values() {
        let Some(depth) = h.depth else {
            continue;
        };
        let a = frame.page(h.at);
        let b = frame.page(std::array::from_fn(|k| h.at[k] + depth * h.direction[k]));
        let d = [b[0] - a[0], b[1] - a[1]];
        let length = d[0].hypot(d[1]);
        if length < 1e-6 {
            continue;
        }
        let r = h.opening_diameter() * frame.s * 0.5;
        let offset = [-r * d[1] / length, r * d[0] / length];
        for sign in [-1.0, 1.0] {
            edge_segments.push([
                [a[0] + sign * offset[0], a[1] + sign * offset[1]],
                [b[0] + sign * offset[0], b[1] + sign * offset[1]],
            ]);
        }
    }
    // A curved side face can have a silhouette between its boundary edges. Keep tags
    // off that face's projected envelope; end-on revolution faces use their rims above.
    let curved: Vec<Rect> = analysis
        .principal_profiles
        .iter()
        .flat_map(|s| &s.curved_faces)
        .filter(|f| {
            f.axis.is_none_or(|a| {
                a.iter()
                    .zip(view.direction)
                    .map(|(x, y)| x * y)
                    .sum::<f64>()
                    .abs()
                    < 0.999
            })
        })
        .map(|f| {
            (0..8)
                .map(|i| frame.page(std::array::from_fn(|k| f.bounds[(i >> k) & 1][k])))
                .fold(
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
        })
        .collect();
    (edge_segments, curved)
}

/// Where the schedule's X/Y origin mark stands, a short leader from the datum corner *origin*: the
/// first position around it, nearest first, whose mark lies inside the frame of a *page*-sized
/// sheet, whose axes its leader does not cross, and which *clear* accepts (given the position and
/// the mark's box).
fn origin_mark(
    origin: [f64; 2],
    page: [f64; 2],
    clear: impl Fn([f64; 2], Rect) -> bool,
) -> Option<[f64; 2]> {
    (0..160).find_map(|step| {
        let distance = 12.0 + (step / 8) as f64 * 2.0;
        let angle = (step % 8) as f64 * std::f64::consts::TAU / 8.0;
        let at = [
            origin[0] + distance * angle.cos(),
            origin[1] + distance * angle.sin(),
        ];
        let bounds = origin_box(at);
        (bounds[0] >= crate::layout::FRAME
            && bounds[1] >= crate::layout::FRAME
            && bounds[2] <= page[0] - crate::layout::FRAME
            && bounds[3] <= page[1] - crate::layout::FRAME
            && !crosses(
                [origin, at],
                [at[0] + 8.5, at[1] - 1.5, at[0] + 11.5, at[1] + 1.5],
            )
            && !crosses(
                [origin, at],
                [at[0] - 1.5, at[1] + 8.5, at[0] + 1.5, at[1] + 11.5],
            )
            && clear(at, bounds))
        .then_some(at)
    })
}

/// The origin mark's box at *at*: its `0`, its X and Y axes and their letters.
fn origin_box(at: [f64; 2]) -> Rect {
    [at[0] - 2.0, at[1] - 2.0, at[0] + 12.0, at[1] + 12.0]
}

/// The place each selected schedule's origin mark takes when nothing but its view's model and
/// centre-line ink is in the way: the first position [`install_at`]'s search tries that is clear
/// of them, before any annotation is placed. (The view, the datum corner and the mark's position
/// on the page, per schedule.)
fn origin_places(
    analysis: &Analysis,
    selection: &Selection,
    page: [f64; 2],
    views: &[PlannedView],
) -> Vec<(String, String, [f64; 2], [f64; 2])> {
    let read: BTreeMap<_, _> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect();
    let mut out = Vec::new();
    for t in &selection.tables {
        let (Some(view), Some(origin)) = (
            views.iter().find(|v| Some(&v.id) == t.view.as_ref()),
            t.origin,
        ) else {
            continue;
        };
        let frame = Frame::new(analysis, view);
        let (edges, curved) = model_ink(analysis, view, &frame, &read);
        let centrelines: Vec<Segment> = view
            .line_work
            .iter()
            .filter(|w| w.kind != "cutting_plane")
            .flat_map(crate::centrelines::segments)
            .collect();
        let origin = frame.page(origin);
        if let Some(at) = origin_mark(origin, page, |_, bounds| {
            curved.iter().all(|q| !overlap(bounds, *q))
                && edges
                    .iter()
                    .chain(&centrelines)
                    .all(|l| !crosses(*l, bounds))
        }) {
            out.push((t.id.clone(), view.id.clone(), origin, at));
        }
    }
    out
}

/// Whether a slot callout (`N× SLOT W × L`, `dims::slots`) laid out with the schedules of a plan
/// that failed to install them takes the place a schedule's origin mark needs ([`origin_places`]).
/// Its wide label seeks the free space a datum corner leaves once the hole callouts go to the
/// schedule (CTC-03), so that plan is laid out again with the place reserved
/// ([`origin_reservations`]). Other failures keep the original dimensions as before.
pub(crate) fn slot_callout_takes_origin(
    analysis: &Analysis,
    plan: &Plan,
    selection: &Selection,
) -> bool {
    origin_places(analysis, selection, plan.sheet.page, &plan.views)
        .into_iter()
        .any(|(_, view, origin, at)| {
            let mark = origin_box(at);
            plan.dimensions.iter().any(|d| {
                d.view == view
                    && d.terms.get(1).is_some_and(|t| t == "SLOT")
                    && d.placed.as_ref().is_some_and(|p| {
                        overlap(mark, p.label_box)
                            || crosses([origin, at], p.label_box)
                            || p.lines.iter().any(|l| crosses(*l, mark))
                    })
            })
        })
}

/// The places the selected schedules' origin marks need ([`origin_places`]), when the selection
/// reserves them, as fixed obstacles for the dimensions laid out with the schedules: a callout's
/// leader and label keep off them, so the schedule does not fail for want of its origin.
pub(crate) fn origin_reservations(
    analysis: &Analysis,
    selection: &Selection,
    page: [f64; 2],
    views: &[PlannedView],
) -> Vec<PlannedDimension> {
    if !selection.reserve_origins {
        return Vec::new();
    }
    origin_places(analysis, selection, page, views)
        .into_iter()
        .map(|(table, view, origin, at)| {
            let label_box = origin_box(at);
            PlannedDimension {
                callout_offset: None,
                reference: false,
                determined_by: Vec::new(),
                region: None,
                id: format!("{table}/origin"),
                kind: "callout".into(),
                view,
                value: 0.0,
                label: "0".into(),
                terms: vec!["0".into()],
                measurements: Vec::new(),
                default: true,
                from_spec: Vec::new(),
                basis: "the schedule's origin mark, reserved before the dimensions are placed"
                    .into(),
                state: crate::items::State::Shown,
                placed: Some(crate::dims::Placed {
                    lines: vec![[origin, at]],
                    arrows: Vec::new(),
                    label_at: [label_box[0], label_box[1]],
                    rotation: 0.0,
                    label_box,
                }),
            }
        })
        .collect()
}

/// Reflow settled rows without regenerating their tags, origins or provenance.
pub(crate) fn reflow_blocks(
    t: &HoleTable,
    sheet: &crate::model::PlannedSheet,
    obstacles: &[Rect],
    fixed: Option<usize>,
) -> Option<Vec<PlannedTable>> {
    if t.view.is_none() {
        return place_rows(&t.rows, sheet, obstacles, fixed);
    }
    let cells = schedule_cells(t);
    let widths = schedule_widths(&cells);
    let (headers, body) = cells.split_at(2);
    let (n, rows, [x0, y0]) = counts(fixed, 2, body.len(), sheet.page).find_map(|n| {
        let rows = split(headers, body, n);
        let size = split_size(&widths, rows[0].len(), n);
        tables::place(size, sheet, obstacles, Corner::TopRight).map(|at| (n, rows, at))
    })?;
    let blocks = rows
        .into_iter()
        .enumerate()
        .map(|(k, rows)| {
            let x = x0 + k as f64 * (widths.iter().sum::<f64>() + BLOCK_GAP);
            let placed = tables::lay(&rows, &widths, [x, y0]);
            PlannedTable {
                sheet: 1,
                id: if n == 1 {
                    t.id.clone()
                } else {
                    format!("{}/{}", t.id, k + 1)
                },
                rows,
                row_claims: Vec::new(),
                default: t.default,
                basis: if n == 1 {
                    t.basis.clone()
                } else {
                    format!("{}; block {} of {n}", t.basis, k + 1)
                },
                state: crate::items::State::Shown,
                placed: Some(placed),
            }
        })
        .collect();
    Some(blocks)
}

fn install_at(analysis: &Analysis, plan: &mut Plan, selection: &Selection) -> bool {
    // Removing ladders must not withdraw the room already needed by a semantic frame or datum.
    // If this presentation cannot keep them in their bands, retain the complete original plan.
    if plan.problems.iter().any(|p| {
        matches!(
            p.code.as_str(),
            "annotation_off_sheet"
                | "annotation_outside_area"
                | "annotation_over_view"
                | "annotation_line_over_view"
        ) && plan.gdt.iter().any(|g| p.subjects.first() == Some(&g.id))
    }) {
        return false;
    }

    let read: BTreeMap<_, _> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect();
    let mut schedules = Vec::new();
    let mut marks = Vec::new();
    let mut obstacles = table_obstacles(analysis, plan, &plan.dimensions);
    for wanted in &selection.tables {
        let view = plan
            .views
            .iter()
            .find(|v| Some(&v.id) == wanted.view.as_ref())
            // Invariant: selection tables were built from the carrying views in this same plan.
            .unwrap();
        let frame = Frame::new(analysis, view);
        let mut t = wanted.clone();
        // Install the form reserved before view layout. Alternate counts are chosen
        // during table reflow, not after the annotation bands have been allocated.
        let n = *counts(columns(&plan.spec, &t.id), 2, t.rows.len(), plan.sheet.page).start();
        let Some(blocks) = reflow_blocks(&t, &plan.sheet, &obstacles, Some(n)) else {
            return false;
        };
        obstacles.extend(
            blocks
                .iter()
                .filter_map(|b| b.placed.as_ref().map(|p| p.cells)),
        );
        t.blocks.extend(blocks);
        let mut labels: Vec<Rect> = plan
            .dimensions
            .iter()
            .filter_map(|d| d.placed.as_ref().map(|p| p.label_box))
            .collect();
        let gdt: Vec<_> = plan
            .gdt
            .iter()
            .filter_map(crate::dims::gdt::proxy)
            .collect();
        labels.extend(
            gdt.iter()
                .filter_map(|g| g.placed.as_ref().map(|p| p.label_box)),
        );
        let local: Vec<_> = plan
            .views
            .iter()
            .filter_map(|v| v.cut.as_ref())
            .filter(|c| c.marked_in == view.id)
            .map(|cut| {
                let [lo, hi] = analysis.envelope;
                let a = frame.page([lo[0], cut.origin[1], lo[2]]);
                let b = frame.page([hi[0], cut.origin[1], hi[2]]);
                (
                    a[0].min(b[0]) - crate::section::OVERRUN,
                    a[0].max(b[0]) + crate::section::OVERRUN,
                    frame.page(cut.origin)[1],
                )
            })
            .collect();
        let has_local = !local.is_empty();
        // A section moved to another sheet is absent from this plan, but its cutting
        // line is still drawn on this view: take it from the view's planned line work.
        let remote = view
            .line_work
            .iter()
            .filter(|w| w.kind == "cutting_plane" && !has_local)
            .filter_map(|w| match w.curves.first() {
                Some(crate::output::Curve::Line(a, b)) => {
                    Some((a[0].min(b[0]), a[0].max(b[0]), a[1]))
                }
                _ => None,
            });
        for (x0, x1, y) in local.into_iter().chain(remote) {
            let (ink, letters) = crate::section::cutting_plane(x0, x1, y);
            labels.extend(
                ink.iter()
                    .filter_map(|i| crate::output::items_bbox(std::slice::from_ref(i))),
            );
            for at in letters {
                labels.push([at[0] - 1.5, at[1] - 1.5, at[0] + 1.5, at[1] + 1.5]);
            }
        }
        let (edge_segments, curved) = model_ink(analysis, view, &frame, &read);
        let mut annotation_lines: Vec<_> = plan
            .dimensions
            .iter()
            .chain(&gdt)
            .filter_map(|d| d.placed.as_ref())
            .flat_map(|p| p.lines.iter().chain(&p.arrows))
            .copied()
            .collect();
        // The view's centre marks, centre lines and pitch circles are drawn ink as well (the
        // cutting-plane line is already a label obstacle above).
        annotation_lines.extend(
            view.line_work
                .iter()
                .filter(|w| w.kind != "cutting_plane")
                .flat_map(crate::centrelines::segments),
        );
        let fixed = fixed_obstacles(analysis, plan, view, Some(&t));
        // Every scheduled hole of this view on the page: a tag must name its own hole, so
        // it is judged against the others' rims as well as against clearance.
        let scheduled: Vec<(&str, [f64; 2], f64)> = t
            .rows
            .iter()
            .map(|r| {
                let h = &read[r.feature.as_str()];
                (
                    r.feature.as_str(),
                    frame.page(h.at),
                    h.opening_diameter() * frame.s * 0.5,
                )
            })
            .collect();
        let lines = Grid::new(edge_segments.iter().chain(&annotation_lines).copied());
        let mut leaders: Vec<Segment> = Vec::new();
        // One tag per page position: coaxial rows (near and far face) share it.
        struct Group {
            tag: String,
            text: String,
            features: Vec<String>,
            principal: Option<usize>,
            hole: [f64; 2],
            radius: f64,
            others: Vec<([f64; 2], f64)>,
            size: [f64; 2],
        }
        let mut groups: Vec<Group> = Vec::new();
        let mut tagged = BTreeSet::new();
        for row in &t.rows {
            if !tagged.insert(row.feature.clone()) {
                continue;
            }
            let members: Vec<_> = t
                .rows
                .iter()
                .filter(|r| {
                    let a = frame.page(read[r.feature.as_str()].at);
                    let b = frame.page(read[row.feature.as_str()].at);
                    (a[0] - b[0]).hypot(a[1] - b[1]) < 1e-4
                })
                .collect();
            tagged.extend(members.iter().map(|r| r.feature.clone()));
            let text = members
                .iter()
                .map(|r| r.tag.as_str())
                .collect::<Vec<_>>()
                .join("/");
            let h = &read[row.feature.as_str()];
            let hole = frame.page(h.at);
            let radius = members
                .iter()
                .map(|r| read[r.feature.as_str()].opening_diameter())
                .fold(0.0, f64::max)
                * frame.s
                * 0.5;
            let others = scheduled
                .iter()
                .filter(|(f, c, _)| {
                    !members.iter().any(|m| m.feature == *f)
                        && (c[0] - hole[0]).hypot(c[1] - hole[1]) > 1e-4
                })
                .map(|&(_, c, r)| (c, r))
                .collect();
            let ink = crate::dims::label_ink(std::slice::from_ref(&text), crate::dims::font());
            let width = (ink.max[0] - ink.min[0]).max(text.len() as f64 * crate::dims::TEXT * 0.6);
            groups.push(Group {
                tag: row.tag.clone(),
                text,
                features: members.iter().map(|r| r.feature.clone()).collect(),
                principal: h.principal.map(|(k, _)| k),
                hole,
                radius,
                others,
                size: [width, crate::dims::TEXT],
            });
        }
        let boxed = |g: &Group, at: [f64; 2]| -> Rect {
            [
                at[0] - g.size[0] * 0.5,
                at[1] - g.size[1] * 0.5,
                at[0] + g.size[0] * 0.5,
                at[1] + g.size[1] * 0.5,
            ]
        };
        let clear = |g: &Group, b: Rect, labels: &[Rect], leaders: &[Segment]| -> bool {
            let padded = [b[0] - 0.6, b[1] - 0.6, b[2] + 0.6, b[3] + 0.6];
            labels.iter().all(|q| !overlap(padded, *q))
                // A curved face's silhouette lies on its projected envelope's sides: the tag
                // may sit wholly inside or outside that envelope, not across it.
                && curved.iter().all(|q| !overlap(padded, *q) || within(padded, *q))
                && lines.all_clear(padded, |l| !crosses(l, padded))
                && leaders.iter().all(|l| !crosses(*l, padded))
                && fixed.iter().all(|q| !overlap(padded, *q))
                && padded[0] >= crate::layout::FRAME
                && padded[1] >= crate::layout::FRAME
                && padded[2] <= plan.sheet.page[0] - crate::layout::FRAME
                && padded[3] <= plan.sheet.page[1] - crate::layout::FRAME
                && read.values().all(|other| {
                    if other
                        .principal
                        .is_none_or(|(k, _)| g.principal.is_none_or(|a| a != k))
                    {
                        return true;
                    }
                    let c = frame.page(other.at);
                    let r = other.opening_diameter() * frame.s * 0.5;
                    // A circular seat's bounding-box corner is not material. Test the
                    // nearest point of the label box against the actual rim instead.
                    rim_gap(b, c, r) > RING * 0.5
                        || (!g.features.contains(&other.feature.id)
                            && [b[0], b[2]]
                                .into_iter()
                                .flat_map(|x| [b[1], b[3]].map(|y| (x - c[0]).hypot(y - c[1])))
                                .all(|d| d < r - RING * 0.5))
                })
        };
        let around = |g: &Group, reach: f64, k: usize| -> [f64; 2] {
            let angle =
                k as f64 * std::f64::consts::TAU / TAG_STEPS as f64 + std::f64::consts::FRAC_PI_4;
            [
                g.hole[0] + angle.cos() * (g.radius + g.size[0] * 0.5 + reach),
                g.hole[1] + angle.sin() * (g.radius + g.size[1] * 0.5 + reach),
            ]
        };
        let mut placed: Vec<Option<([f64; 2], Option<Segment>)>> = vec![None; groups.len()];
        // Beside the hole first, for every tag: no leader, so the tag must be plainly nearer
        // its own hole than any other scheduled hole, by rim gap and by centre distance.
        for (g, slot) in groups.iter().zip(&mut placed) {
            'near: for ring in 0..TAG_NEAR {
                for k in 0..TAG_STEPS {
                    let at = around(g, 1.0 + ring as f64, k);
                    let b = boxed(g, at);
                    if names_own(b, at, g.hole, g.radius, &g.others)
                        && clear(g, b, &labels, &leaders)
                    {
                        *slot = Some((at, None));
                        labels.push(b);
                        break 'near;
                    }
                }
            }
        }
        // The rest a short leader from the hole's rim, which says which hole the tag names.
        // Placed greedily; a tag left without a place goes first on the next attempt.
        let (near_labels, near_leaders, near_placed) =
            (labels.clone(), leaders.clone(), placed.clone());
        let mut order: Vec<usize> = (0..groups.len()).filter(|&i| placed[i].is_none()).collect();
        let mut attempts = 0;
        'attempt: loop {
            labels.clone_from(&near_labels);
            leaders.clone_from(&near_leaders);
            placed.clone_from(&near_placed);
            for n in 0..order.len() {
                let g = &groups[order[n]];
                // Leaders fan outward from their neighbours (radially, on a bolt circle).
                let near: Vec<_> = g
                    .others
                    .iter()
                    .filter(|(c, _)| (c[0] - g.hole[0]).hypot(c[1] - g.hole[1]) < TAG_NEIGHBOURS)
                    .collect();
                let outward = (!near.is_empty()).then(|| {
                    let n = near.len() as f64;
                    let mid = near
                        .iter()
                        .fold([0.0, 0.0], |a, (c, _)| [a[0] + c[0] / n, a[1] + c[1] / n]);
                    (g.hole[1] - mid[1]).atan2(g.hole[0] - mid[0])
                });
                // The shortest leader that crosses fewest model lines, nearest outward.
                let mut best: Option<(f64, [f64; 2], Segment)> = None;
                for ring in 0..TAG_FAR {
                    let r = 3.0 + ring as f64;
                    if best.is_some_and(|(cost, _, _)| cost < r) {
                        break;
                    }
                    for k in 0..TAG_STEPS {
                        let at = around(g, r, k);
                        let b = boxed(g, at);
                        // A blocked label cannot win at any crossing cost. Check
                        // the unchanged clearance predicate before scoring its
                        // leader through a dense model's projected edges.
                        if !clear(g, b, &labels, &leaders) {
                            continue;
                        }
                        let Some(leader) = tag_leader(g.hole, g.radius, at, b) else {
                            continue;
                        };
                        let length =
                            (leader[1][0] - leader[0][0]).hypot(leader[1][1] - leader[0][1]);
                        let turn = outward.map_or(0.0, |out| {
                            let a = (at[1] - g.hole[1]).atan2(at[0] - g.hole[0]) - out;
                            a.sin().atan2(a.cos()).abs()
                        });
                        let bound = best.map_or(f64::INFINITY, |(c, _, _)| c);
                        let mut cost = length + TAG_TURN * turn;
                        let span = [
                            leader[0][0].min(leader[1][0]),
                            leader[0][1].min(leader[1][1]),
                            leader[0][0].max(leader[1][0]),
                            leader[0][1].max(leader[1][1]),
                        ];
                        lines.all_clear(span, |e| {
                            if segments_cross(leader, e) {
                                cost += TAG_CROSSING;
                            }
                            cost < bound
                        });
                        if cost >= bound {
                            continue;
                        }
                        if leader_clear(leader, &labels, &leaders, &g.others) {
                            best = Some((cost, at, leader));
                        }
                    }
                }
                let Some((_, at, leader)) = best else {
                    attempts += 1;
                    if attempts > order.len().min(TAG_ATTEMPTS) {
                        return false;
                    }
                    let first = order.remove(n);
                    order.insert(0, first);
                    continue 'attempt;
                };
                placed[order[n]] = Some((at, Some(leader)));
                labels.push(boxed(g, at));
                leaders.push(leader);
            }
            break;
        }
        for (g, slot) in groups.into_iter().zip(placed) {
            // Invariant: The placement loop returns false on a missing slot before reaching this zip.
            let (at, leader) = slot.expect("every tag placed");
            marks.push(Balloon {
                id: format!("{}/tag/{}", t.id, g.tag),
                tag: g.text,
                view: view.id.clone(),
                features: g.features,
                certifying: true,
                interior: false,
                at,
                radius: 0.0,
                label_box: None,
                leader,
                state: crate::items::State::Shown,
            });
        }
        // X/Y origin, drawn at the view's datum corner, with short coordinate axes.
        // Invariant: Schedule selection requires a resolved origin.
        let origin = frame.page(t.origin.unwrap());
        let position = origin_mark(origin, plan.sheet.page, |at, bounds| {
            labels
                .iter()
                .chain(fixed.iter())
                .all(|b| !overlap(bounds, *b))
                && curved.iter().all(|q| !overlap(bounds, *q))
                && edge_segments
                    .iter()
                    .chain(&annotation_lines)
                    .chain(&leaders)
                    .all(|l| !crosses(*l, bounds))
                && leaders.iter().all(|l| !segments_cross(*l, [origin, at]))
                && labels.iter().all(|b| !crosses([origin, at], *b))
        });
        let Some(at) = position else {
            return false;
        };
        marks.push(Balloon {
            id: format!("{}/origin", t.id),
            tag: "0".into(),
            view: view.id.clone(),
            features: Vec::new(),
            certifying: false,
            interior: false,
            at,
            radius: 0.0,
            label_box: None,
            leader: Some([origin, at]),
            state: crate::items::State::Shown,
        });
        for (name, tip, end, text) in [
            (
                "x",
                [at[0] + 2.0, at[1]],
                [at[0] + 7.0, at[1]],
                [at[0] + 10.0, at[1]],
            ),
            (
                "y",
                [at[0], at[1] + 2.0],
                [at[0], at[1] + 7.0],
                [at[0], at[1] + 10.0],
            ),
        ] {
            marks.push(Balloon {
                id: format!("{}/origin/{name}", t.id),
                tag: name.to_uppercase(),
                view: view.id.clone(),
                features: Vec::new(),
                certifying: false,
                interior: false,
                at: text,
                radius: 0.0,
                label_box: None,
                leader: Some([end, tip]),
                state: crate::items::State::Shown,
            });
        }
        schedules.push(t);
    }
    for d in &selection.dimensions {
        let mut d = d.clone();
        let by = schedules
            .iter()
            .find(|t| t.view.as_ref() == Some(&d.view))
            // Invariant: A schedule was just created for each carrying view in selection.dimensions.
            .unwrap()
            .id
            .clone();
        d.placed = None;
        d.state = crate::items::State::Covered { by: by.clone() };
        d.basis += &format!("; stated by {by}");
        if d.kind == "linear" {
            // These spans no longer belong to the dimension ladder. Their stable covered IDs
            // stay in the same omission ledger as other statements superseded by an owner.
            schedules
                .iter_mut()
                .find(|t| t.id == by)
                // Invariant: by is the id of the schedule just found in this unchanged vector.
                .unwrap()
                .covered
                .extend(d.measurements);
            plan.omitted.push(crate::dims::Omitted {
                id: d.id,
                label: d.label,
                basis: d.basis,
                state: d.state,
            });
        } else {
            plan.dimensions.push(d);
        }
    }
    // A source-owned coordinate may already have its covered entry in the
    // replanned ladder. Keep that original label and provenance exactly once.
    let mut ids: BTreeSet<_> = plan
        .dimensions
        .iter()
        .map(|d| d.id.clone())
        .chain(plan.omitted.iter().map(|o| o.id.clone()))
        .collect();
    plan.omitted.extend(
        selection
            .aliases
            .iter()
            .filter(|o| ids.insert(o.id.clone()))
            .cloned(),
    );
    plan.hole_tables = schedules;
    for mark in &mut marks {
        let ink = crate::dims::label_ink(std::slice::from_ref(&mark.tag), crate::dims::font());
        let width = (ink.max[0] - ink.min[0]).max(mark.tag.len() as f64 * crate::dims::TEXT * 0.6);
        mark.label_box = Some([
            mark.at[0] - width * 0.5,
            mark.at[1] - crate::dims::TEXT * 0.5,
            mark.at[0] + width * 0.5,
            mark.at[1] + crate::dims::TEXT * 0.5,
        ]);
    }
    plan.balloons = marks;
    plan.problems.retain(|p| p.code != "pinned_item_not_drawn");
    crate::placement_intent::finish(plan, analysis);
    true
}
