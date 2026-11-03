//! Spend spare paper on blank view gutters, only after sheet/scale recovery and allocation.
//! All packers (including relative placements, details and measured refits) meet here.
use crate::layout::{Rect, margin};
use crate::model::{Analysis, Plan};
use crate::placement_intent::{move_box, move_point};

// About eight annotation capitals: ample separation on sparse sheets while the
// projections still read as one set. Tight sheets share less, down to zero.
pub(crate) const PREFERRED: f64 = 25.0;
/// Spare paper also moves views away from the frame (maintainer 2026-10-25): after the gaps
/// between views have their share, the whole composition on each sheet moves as one block so
/// each band keeps up to this much more than the frame gutter every drawing keeps
/// ([`crate::layout::FRAME_GUTTER`]). Moving rigidly keeps an edit between views from moving
/// the composition.
pub(crate) const FRAME_SPACE: f64 = 10.0;

fn include(a: &mut Rect, b: Rect) {
    for k in 0..2 {
        a[k] = a[k].min(b[k]);
        a[k + 2] = a[k + 2].max(b[k + 2]);
    }
}

fn measured(plan: &Plan, v: &crate::model::PlannedView) -> bool {
    let name = v.id.trim_start_matches("v:");
    (v.id == "v:iso" || v.cut.is_some())
        // Authored relations use the literal footprint, including the anchor's.
        // Cross-sheet footprints also reserve reference text and define zones.
        && v.xref.is_none()
        && v.cut.as_ref().is_none_or(|c| c.xref.is_none())
        && plan.spec.views.get(name).is_none_or(|v| v.place.is_none())
        && !plan.spec.views.values().any(|v| {
            v.place.as_ref().is_some_and(|p| p.relative_to == name)
        })
}

/// Reserved annotation bands, enlarged to contain settled ink and view furniture.
/// Empty iso fitting regions and generic section padding are available paper.
pub(crate) fn bands(analysis: &Analysis, plan: &Plan) -> Vec<Rect> {
    let mut bounds: Vec<_> = plan
        .views
        .iter()
        .map(|v| {
            if measured(plan, v) && v.id == "v:iso" {
                let b = analysis.drawing_iso_bounds();
                [
                    v.at[0] + v.scale * b[0],
                    v.at[1] + v.scale * b[1],
                    v.at[0] + v.scale * b[2],
                    v.at[1] + v.scale * b[3],
                ]
            } else if measured(plan, v) {
                let (size, _) = crate::detail::crop_extent(analysis, v);
                [
                    v.at[0] - v.scale * size[0] / 2.0,
                    v.at[1] - v.scale * size[1] / 2.0,
                    v.at[0] + v.scale * size[0] / 2.0,
                    v.at[1] + v.scale * size[1] / 2.0,
                ]
            } else {
                v.area
            }
        })
        .collect();
    let mut own = |id: &str, b: Rect| {
        if let Some(i) = plan.views.iter().position(|v| v.id == id) {
            include(&mut bounds[i], b);
        }
    };
    // Label clearance is occupied paper too, including beside the title and
    // fixed furniture. Keep the two mm used by measured annotation packing.
    let label_clearance = |b: Rect| [b[0] - 2.0, b[1] - 2.0, b[2] + 2.0, b[3] + 2.0];
    let text_box = |text: &str, at: [f64; 2]| {
        let laid = crate::dims::font().text(text, crate::section::FONT_SIZE);
        label_clearance([
            at[0] + laid.min[0],
            at[1] + laid.min[1],
            at[0] + laid.max[0],
            at[1] + laid.max[1],
        ])
    };
    let reference_box = |prefix: &str, r: &crate::sheets::CrossRef| {
        text_box(
            &format!("{prefix} SHEET {}, ZONE {}", r.sheet, r.zone),
            r.at,
        )
    };
    for d in &plan.dimensions {
        if let Some(p) = &d.placed {
            own(&d.view, label_clearance(p.label_box));
        }
        if let Some(b) = crate::dims::ink_box(d) {
            own(&d.view, b);
        }
    }
    for g in &plan.gdt {
        if let Some(p) = &g.placed {
            own(&g.view, label_clearance(p.label_box));
        }
        if let Some(d) = crate::dims::gdt::proxy(g)
            && let Some(b) = crate::dims::ink_box(&d)
        {
            own(&g.view, b);
        }
    }
    for c in &plan.captions {
        own(&c.view, c.ink);
    }
    for b in &plan.balloons {
        let mut r = [
            b.at[0] - b.radius,
            b.at[1] - b.radius,
            b.at[0] + b.radius,
            b.at[1] + b.radius,
        ];
        if let Some(label) = b.label_box {
            include(&mut r, label);
        }
        for p in b.leader.iter().flatten() {
            include(&mut r, [p[0], p[1], p[0], p[1]]);
        }
        own(&b.view, r);
    }
    for v in &plan.views {
        if let Some(r) = &v.xref {
            own(&v.id, reference_box("FROM", r));
        }
        if let Some(c) = &v.cut
            && let Some(r) = &c.xref
        {
            own(
                &c.marked_in,
                reference_box(&format!("SECTION {}–{},", c.label, c.label), r),
            );
        }
        for w in &v.line_work {
            for c in &w.curves {
                own(&v.id, c.bbox());
            }
        }
        // With one view, furniture_bounds returns the fixed sheet furniture first,
        // then its caption and (for a detail) the source marker. Resolve ownership.
        let sheet = std::iter::once(&plan.sheet)
            .chain(&plan.sheets)
            .find(|s| s.number == v.sheet)
            // Invariant: Sheet assignments are validated before gutters are solved.
            .expect("view sheet");
        let fixed = crate::sheet_fit::furniture_bounds(analysis, sheet, &[]).len();
        let furniture =
            crate::sheet_fit::furniture_bounds(analysis, sheet, std::slice::from_ref(v));
        for b in furniture.into_iter().skip(fixed) {
            own(&v.id, label_clearance(b));
        }
        // View captions render in the annotation font; the shared furniture
        // helper also serves condensed title text. Reserve the actual caption ink.
        if let Some(c) = &v.cut {
            let (size, _) = crate::detail::crop_extent(analysis, v);
            let at = [v.at[0], v.at[1] - v.scale * size[1] / 2.0 - 7.0];
            own(
                &v.id,
                text_box(&format!("SECTION {}–{}", c.label, c.label), at),
            );
        }
        if let Some(d) = &v.detail {
            own(
                &v.id,
                text_box(&crate::detail::caption(d), crate::detail::caption_at(v)),
            );
        }
        if let Some(d) = &v.detail
            && let Some(source) = plan.views.iter().find(|s| s.id == d.source_view)
        {
            let (_, at) = crate::detail::marker(analysis, d, source, &plan.views, sheet.page, &[]);
            let text = crate::dims::font().text(&d.label, crate::section::FONT_SIZE);
            own(
                &source.id,
                [
                    at[0] + text.min[0],
                    at[1] + text.min[1],
                    at[0] + text.max[0],
                    at[1] + text.max[1],
                ],
            );
        }
    }
    bounds
}

// Difference constraints: translation[to] <= translation[from] + limit.
#[derive(Clone, Copy)]
struct Edge {
    from: usize,
    to: usize,
    limit: f64,
}

fn solve(n: usize, edges: &[Edge]) -> Option<Vec<f64>> {
    let mut d = vec![0.0; n];
    for pass in 0..n {
        let mut changed = false;
        for &Edge { from, to, limit } in edges {
            if d[to] > d[from] + limit {
                d[to] = d[from] + limit;
                changed = true;
            }
        }
        if !changed {
            let zero = d[n - 1];
            for x in &mut d {
                *x -= zero;
            }
            return Some(d);
        }
        if pass == n - 1 {
            return None;
        }
    }
    // Invariant: Bellman-Ford returns in the final pass either as converged or infeasible.
    unreachable!()
}

/// Keep each pair on its established side. This preserves the drawing's composition
/// and turns a non-convex packing problem into two small difference-constraint systems.
fn separation(a: Rect, b: Rect) -> (usize, bool, f64) {
    let choices = [
        (0, true, b[0] - a[2]),
        (0, false, a[0] - b[2]),
        (1, true, b[1] - a[3]),
        (1, false, a[1] - b[3]),
    ];
    // Invariant: choices contains both separation axes for the nonoverlapping established boxes.
    *choices.iter().max_by(|a, b| a.2.total_cmp(&b.2)).unwrap()
}

fn translations(
    analysis: &Analysis,
    plan: &Plan,
    bounds: &[Rect],
    preferred: f64,
    above_title: bool,
) -> Option<Vec<[f64; 2]>> {
    let n = bounds.len();
    let mut hard = [Vec::new(), Vec::new()];
    let mut soft = [Vec::new(), Vec::new()];
    let mut frame_soft = [Vec::new(), Vec::new()];
    let equality = |edges: &mut Vec<Edge>, i, j| {
        edges.extend([
            Edge {
                from: i,
                to: j,
                limit: 0.0,
            },
            Edge {
                from: j,
                to: i,
                limit: 0.0,
            },
        ]);
    };
    let index = |id: &str| plan.views.iter().position(|v| v.id == id);
    // Projection origins remain aligned even when the two annotation bands differ.
    for (id, k) in [("v:plan", 0), ("v:side", 1)] {
        if let (Some(i), Some(j)) = (index("v:front"), index(id))
            && plan.views[i].sheet == plan.views[j].sheet
        {
            equality(&mut hard[k], i, j);
        }
    }
    // The packer establishes rows/columns for sections and auxiliary projections
    // too. Keep their established alignment, even when annotation bands differ.
    // Isometrics and detail crops are independent compositions.
    for (i, a) in plan.views.iter().enumerate() {
        if a.id == "v:iso" || a.detail.is_some() {
            continue;
        }
        for (j, b) in plan.views[..i].iter().enumerate() {
            if a.sheet != b.sheet || b.id == "v:iso" || b.detail.is_some() {
                continue;
            }
            for (k, edges) in hard.iter_mut().enumerate() {
                if (a.at[k] - b.at[k]).abs() < 1e-6 {
                    equality(edges, i, j);
                }
            }
            // A section may already have been raised to clear the title. It is
            // still beside the row: keep that deliberate offset rather than
            // letting the gutter pass pull the row and section apart again.
            let same_up = a.up.iter().zip(b.up).map(|(x, y)| x * y).sum::<f64>().abs() > 1.0 - 1e-6;
            let x_gap = (bounds[i][0] - bounds[j][2]).max(bounds[j][0] - bounds[i][2]);
            let y_overlap = bounds[i][3].min(bounds[j][3]) - bounds[i][1].max(bounds[j][1]);
            if same_up && x_gap >= -1e-6 && y_overlap > 1e-6 {
                equality(&mut hard[1], i, j);
            }
        }
    }
    for (i, v) in plan.views.iter().enumerate() {
        // Explicit placement relations retain their exact gap and alignment, pinned
        // or otherwise. Their connected group may translate, but never deform.
        if let Some(p) = plan
            .spec
            .views
            .get(v.id.trim_start_matches("v:"))
            .and_then(|s| s.place.as_ref())
            && let Some(j) = index(&format!("v:{}", p.relative_to))
        {
            for edges in &mut hard {
                equality(edges, i, j);
            }
        }
        let pinned_item = plan
            .spec
            .dimensions
            .iter()
            .filter_map(|(id, d)| d.place.as_ref().filter(|p| p.pinned).map(|_| id))
            .chain(
                plan.spec
                    .annotations
                    .iter()
                    .filter_map(|(id, a)| a.place.as_ref().filter(|p| p.pinned).map(|_| id)),
            )
            .any(|id| crate::placement_intent::carrier(plan, id).as_deref() == Some(&v.id));
        let pinned_view = plan
            .spec
            .views
            .get(v.id.trim_start_matches("v:"))
            .and_then(|s| s.place.as_ref())
            .is_some_and(|p| p.pinned);
        let pinned = pinned_item || pinned_view
            // Cross-sheet references encode both destination zones and local stations.
            || v.xref.is_some() || v.cut.as_ref().is_some_and(|c| c.xref.is_some())
            || plan.views.iter().any(|section| section.cut.as_ref().is_some_and(|c| {
                c.marked_in == v.id && c.xref.is_some()
            }));
        if pinned {
            for edges in &mut hard {
                equality(edges, i, n);
            }
        }
        let sheet = std::iter::once(&plan.sheet)
            .chain(&plan.sheets)
            .find(|s| s.number == v.sheet)
            // Invariant: Sheet assignments are validated before gutters are solved.
            .expect("view sheet");
        // Principal bands already contain blank annotation padding. The iso band
        // is measured ink, so keep its caption one mm inside the drawn frame.
        let inset = margin() + if v.id == "v:iso" { 1.0 } else { 0.0 };
        let edge = FRAME_SPACE.min(preferred);
        for k in 0..2 {
            let (low, high) = (
                (bounds[i][k] - inset - 1e-5).max(0.0),
                (sheet.page[k] - inset - bounds[i][k + 2] - 1e-5).max(0.0),
            );
            hard[k].push(Edge {
                from: i,
                to: n,
                limit: low,
            });
            hard[k].push(Edge {
                from: n,
                to: i,
                limit: high,
            });
            // The frame is a neighbour too: spare paper opens its gaps as it does a pair's.
            frame_soft[k].push((i, n, low, edge));
            frame_soft[k].push((n, i, high, edge));
        }
        let mut obstacles = vec![sheet.title_block];
        obstacles.extend(crate::sheet_fit::furniture_bounds(analysis, sheet, &[]));
        if sheet.number == 1 {
            obstacles.extend(
                plan.notes_block
                    .iter()
                    .chain(&plan.radius_notes_block)
                    .map(|b| b.cells),
            );
            obstacles.extend(
                plan.requirements
                    .iter()
                    .filter_map(|r| r.placed.as_ref().map(|p| p.label_box)),
            );
        }
        // Table placement reserves PAD around its cells. Preserve that corridor
        // when spending spare paper: canonical pin replays rebuild the tables in
        // these frames, and must be able to install the same presentation again.
        // The iso's bounds are its measured ink, not a padded band: keep a view gutter
        // between that ink and a table, as the planner placed it.
        let table_clearance = |b: Rect| {
            let p = if v.id == "v:iso" {
                crate::layout::VIEW_GUTTER
            } else {
                crate::tables::PAD
            };
            [b[0] - p, b[1] - p, b[2] + p, b[3] + p]
        };
        obstacles.extend(
            plan.tables
                .iter()
                .filter(|t| t.sheet == sheet.number)
                .filter_map(|t| t.placed.as_ref().map(|p| table_clearance(p.cells))),
        );
        obstacles.extend(
            plan.hole_tables
                .iter()
                .filter(|t| t.sheet == sheet.number)
                .flat_map(|t| &t.blocks)
                .filter_map(|t| t.placed.as_ref().map(|p| table_clearance(p.cells))),
        );
        if let Some(t) = &plan.radius_table
            && sheet.number == t.table.sheet
            && let Some(p) = &t.table.placed
        {
            obstacles.push(table_clearance(p.cells));
        }
        for (obstacle, b) in obstacles.into_iter().enumerate() {
            // A row initially left of the title may have room to lift above it.
            // Try that composition too, before sacrificing a useful gutter.
            if above_title && obstacle == 0 && v.id != "v:iso" {
                hard[1].push(Edge {
                    from: i,
                    to: n,
                    limit: bounds[i][1] - b[3] - 1e-5,
                });
                continue;
            }
            let (k, forward, gap) = separation(bounds[i], b);
            // Never worsen an existing overlap; gutters cannot invalidate a drawing.
            hard[k].push(if forward {
                Edge {
                    from: n,
                    to: i,
                    limit: (gap - 1e-5).max(0.0),
                }
            } else {
                Edge {
                    from: i,
                    to: n,
                    limit: (gap - 1e-5).max(0.0),
                }
            });
        }
        for j in 0..i {
            if plan.views[j].sheet != v.sheet {
                continue;
            }
            let (k, forward, gap) = separation(bounds[j], bounds[i]);
            let (from, to) = if forward { (i, j) } else { (j, i) };
            // A rigid explicit relation can have a deliberately zero gap.
            let explicit = |a: usize, b: usize| {
                plan.spec
                    .views
                    .get(plan.views[a].id.trim_start_matches("v:"))
                    .and_then(|v| v.place.as_ref())
                    .is_some_and(|p| format!("v:{}", p.relative_to) == plan.views[b].id)
            };
            if explicit(i, j) || explicit(j, i) {
                continue;
            }
            soft[k].push((from, to, gap, preferred));
        }
    }
    // Spend slack on *soft* gaps over *base* constraints: start from the known feasible
    // composition, share tight slack before improving individual gaps (no early gap may consume
    // all the paper and leave another touching), then improve each in turn.
    let spend = |base: Vec<Edge>, soft: &[(usize, usize, f64, f64)]| -> Option<Vec<Edge>> {
        let mut edges = base;
        let first = edges.len();
        for &(from, to, gap, _) in soft {
            edges.push(Edge {
                from,
                to,
                limit: gap - gap.min(0.0),
            });
        }
        solve(n + 1, &edges)?;
        let set_common = |edges: &mut [Edge], fraction: f64| {
            for (edge, &(_, _, gap, wanted)) in edges[first..].iter_mut().zip(soft) {
                let floor = gap.min(0.0);
                edge.limit = gap - (floor + fraction * (wanted - floor));
            }
        };
        set_common(&mut edges, 1.0);
        if solve(n + 1, &edges).is_none() {
            let (mut lo, mut hi) = (0.0, 1.0);
            for _ in 0..32 {
                let mid = (lo + hi) / 2.0;
                set_common(&mut edges, mid);
                if solve(n + 1, &edges).is_some() {
                    lo = mid;
                } else {
                    hi = mid;
                }
            }
            set_common(&mut edges, lo);
        }
        for slot in first..edges.len() {
            let (_, _, gap, wanted) = soft[slot - first];
            let target = gap - wanted;
            let old = edges[slot].limit;
            edges[slot].limit = target;
            if solve(n + 1, &edges).is_none() {
                let (mut lo, mut hi) = (target, old);
                for _ in 0..32 {
                    let mid = (lo + hi) / 2.0;
                    edges[slot].limit = mid;
                    if solve(n + 1, &edges).is_some() {
                        hi = mid;
                    } else {
                        lo = mid;
                    }
                }
                edges[slot].limit = hi;
            }
        }
        Some(edges)
    };
    let mut result = vec![[0.0; 2]; n];
    for k in 0..2 {
        // The views' gaps first, as they were; the frame's gaps only from the paper left over,
        // so a label that changes a gap between views does not move the composition.
        let mut edges = spend(hard[k].clone(), &soft[k])?;
        // The composition then moves as one block on each sheet, away from the frame.
        let d = solve(n + 1, &edges)?;
        for i in 0..n {
            for j in 0..i {
                if plan.views[i].sheet == plan.views[j].sheet {
                    // A nanometre of play: exact opposite limits can sum below zero in
                    // floating point, which the solver reads as an infeasible cycle.
                    edges.push(Edge {
                        from: i,
                        to: j,
                        limit: d[j] - d[i] + 1e-9,
                    });
                    edges.push(Edge {
                        from: j,
                        to: i,
                        limit: d[i] - d[j] + 1e-9,
                    });
                }
            }
        }
        let edges = spend(edges, &frame_soft[k])?;
        let d = solve(n + 1, &edges)?;
        for i in 0..n {
            result[i][k] = d[i];
        }
    }
    Some(result)
}

pub(crate) fn apply(analysis: &Analysis, plan: &mut Plan) {
    let replay = plan.replay_gutters.take();
    let preferred = plan.spec.sheet.view_gutter.unwrap_or(PREFERRED);
    if plan.spec.sheet.keep_layout == Some(true) || preferred == 0.0 || plan.views.len() < 2 {
        return;
    }
    let bounds = bands(analysis, plan);
    let mut deltas = translations(analysis, plan, &bounds, preferred, false)
        // Invariant: The unchanged established coordinates satisfy the constraints before optional gutter moves.
        .expect("the established composition remains feasible");
    if let Some(lifted) = translations(analysis, plan, &bounds, preferred, true) {
        let score = |d: &[[f64; 2]]| {
            let mut smallest = preferred;
            let mut total = 0.0;
            for i in 0..bounds.len() {
                for j in 0..i {
                    if plan.views[i].sheet != plan.views[j].sheet {
                        continue;
                    }
                    let mut a = bounds[i];
                    let mut b = bounds[j];
                    move_box(&mut a, d[i]);
                    move_box(&mut b, d[j]);
                    let gap = separation(a, b).2.min(preferred);
                    smallest = smallest.min(gap);
                    total += gap;
                }
            }
            (smallest, total)
        };
        let a = score(&deltas);
        let b = score(&lifted);
        if b.0 > a.0 + 1e-6 || ((b.0 - a.0).abs() < 1e-6 && b.1 > a.1 + 1e-6) {
            deltas = lifted;
        }
    }
    // Below the bisection resolution, preserve an already settled frame exactly.
    for d in deltas.iter_mut().flatten() {
        if d.abs() < 1e-8 {
            *d = 0.0;
        } else {
            // Different annotation bounds can leave nanometre-sized bisection
            // noise on the same frame. Keep nonzero moves at that resolution too.
            *d = (*d * 1e8).round() / 1e8;
        }
    }
    // A pin replay holds its first sheet's frames, so the solve above cannot spend
    // spare paper there. Its later sheets are packed afresh, exactly as in the canonical
    // drawing: give them that drawing's gutters rather than a solve coupled to the
    // pinned first sheet.
    if let Some(replay) = &replay {
        for (v, d) in plan.views.iter().zip(deltas.iter_mut()) {
            if v.sheet > 1 {
                *d = replay.get(&v.id).copied().unwrap_or([0.0; 2]);
            }
        }
    }
    transport(analysis, plan, bounds, deltas);
}

/// Translate each view and its annotations by its gutter delta, and record the delta.
pub(crate) fn transport(
    analysis: &Analysis,
    plan: &mut Plan,
    bounds: Vec<Rect>,
    deltas: Vec<[f64; 2]>,
) {
    // Rebase editor tip metadata from the transported paper point. Keeping the old
    // relative offset can lose an ulp when added to a translated view origin, so a
    // no-op offset pin would otherwise change its reported tip.
    let tips: Vec<_> = plan
        .callout_tips
        .keys()
        .filter_map(|id| {
            let (view, mut tip) = plan.automatic_callout_tip(id)?;
            if plan
                .spec
                .dimensions
                .get(id)
                .is_none_or(|d| d.place.is_none())
                && let Some(p) = plan
                    .dimensions
                    .iter()
                    .find(|d| d.id == *id && d.view == view)
                    .and_then(|d| d.placed.as_ref())
                    .and_then(|p| p.lines.first())
            {
                tip = p[0];
            }
            let i = plan.views.iter().position(|v| v.id == view)?;
            move_point(&mut tip, deltas[i]);
            Some((id.clone(), i, tip))
        })
        .collect();
    let measured: Vec<_> = plan.views.iter().map(|v| measured(plan, v)).collect();
    for (((v, b), delta), measured) in plan.views.iter_mut().zip(bounds).zip(&deltas).zip(measured)
    {
        move_point(&mut v.at, *delta);
        // A canonical pin already carries the measured frame. Recomputing the
        // same bounds from its transported origin can lose an ulp on a no-op.
        if measured && v.area.iter().zip(b).any(|(a, b)| (a - b).abs() > 1e-8) {
            v.area = b;
        }
        move_box(&mut v.area, *delta);
        crate::centrelines::shift(v, *delta);
    }
    let delta = |id: &str| {
        plan.views
            .iter()
            .position(|v| v.id == id)
            .map_or([0.0; 2], |i| deltas[i])
    };
    for d in &mut plan.dimensions {
        if let Some(p) = &mut d.placed {
            let d = delta(&d.view);
            move_point(&mut p.label_at, d);
            move_box(&mut p.label_box, d);
            for q in p.lines.iter_mut().chain(&mut p.arrows).flatten() {
                move_point(q, d);
            }
        }
    }
    for g in &mut plan.gdt {
        if let Some(p) = &mut g.placed {
            let d = delta(&g.view);
            move_point(&mut p.tip, d);
            move_box(&mut p.label_box, d);
            for q in p
                .lines
                .iter_mut()
                .chain(&mut p.arrows)
                .chain(&mut p.strokes)
                .flatten()
                .chain(p.fills.iter_mut().flatten())
            {
                move_point(q, d);
            }
            for arc in &mut p.arcs {
                arc[0] += d[0];
                arc[1] += d[1];
            }
            for t in p.letters.iter_mut().chain(&mut p.runs) {
                move_point(&mut t.at, d);
            }
        }
    }
    for c in &mut plan.captions {
        let d = delta(&c.view);
        move_point(&mut c.at, d);
        move_box(&mut c.ink, d);
    }
    for b in &mut plan.balloons {
        let d = delta(&b.view);
        move_point(&mut b.at, d);
        if let Some(label) = &mut b.label_box {
            move_box(label, d);
        }
        for p in b.leader.iter_mut().flatten() {
            move_point(p, d);
        }
    }
    for (id, i, tip) in tips {
        let v = &plan.views[i];
        plan.callout_tips
            .insert(id, (v.id.clone(), [tip[0] - v.at[0], tip[1] - v.at[1]]));
    }
    for (v, d) in plan.views.iter().zip(&deltas) {
        let total = plan.gutter_deltas.entry(v.id.clone()).or_insert([0.0; 2]);
        total[0] += d[0];
        total[1] += d[1];
    }
    plan.sheet.fill = crate::sheet_fit::fill(analysis, plan);
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sections_and_auxiliary_projections_translate_with_their_rows_and_columns() {
        let (_, analysis) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/samples/editor-plate.step"),
        )
        .unwrap();
        let mut plan = crate::model::plan_recovered(&analysis, &Default::default()).unwrap();
        plan.views.retain(|v| v.id != "v:iso");
        let mut section = plan.views[0].clone();
        section.id = "v:section_aa".into();
        let mut auxiliary = section.clone();
        auxiliary.id = "v:auxiliary".into();
        plan.views.extend([section, auxiliary]);
        let bounds = vec![
            [10.0, 10.0, 30.0, 30.0],
            [10.0, 30.0, 30.0, 50.0],
            [30.0, 10.0, 50.0, 30.0],
            [50.0, 10.0, 70.0, 30.0],
            [10.0, 50.0, 30.0, 70.0],
        ];
        for (v, b) in plan.views.iter_mut().zip(&bounds) {
            v.at = [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])];
        }
        for lift in [0.0, 5.0] {
            let mut bounds = bounds.clone();
            bounds[3][1] += lift;
            bounds[3][3] += lift;
            plan.views[3].at[1] += lift;
            let d = translations(&analysis, &plan, &bounds, PREFERRED, false).unwrap();
            assert!(d[1][1] > 1.0 && d[2][0] > 1.0);
            assert_eq!(d[0][1], d[2][1]);
            assert_eq!(d[0][1], d[3][1]);
            assert_eq!(d[0][0], d[1][0]);
            assert_eq!(d[0][0], d[4][0]);
        }
    }

    #[test]
    fn final_gutters_hold_view_pins_their_anchors_and_covered_item_carriers() {
        let (_, analysis) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/samples/editor-plate.step"),
        )
        .unwrap();
        let mut plan = crate::model::plan_recovered(&analysis, &Default::default()).unwrap();
        plan.views.retain(|v| v.id != "v:iso");
        let bounds = vec![
            [10.0, 10.0, 30.0, 30.0],
            [10.0, 30.0, 30.0, 50.0],
            [30.0, 10.0, 50.0, 30.0],
        ];
        assert_eq!(
            plan.views.iter().map(|v| v.id.as_str()).collect::<Vec<_>>(),
            ["v:front", "v:plan", "v:side"]
        );
        let free = translations(&analysis, &plan, &bounds, PREFERRED, false).unwrap();
        assert!(free[1][1] > 1.0 && free[2][0] > 1.0);

        plan.spec = serde_json::from_value(json!({"views":{"plan":{"place":{
            "relative_to":"front", "side":"above", "gap":0, "pinned":true
        }}}}))
        .unwrap();
        let pinned = translations(&analysis, &plan, &bounds, PREFERRED, false).unwrap();
        assert_eq!(pinned[0], [0.0; 2]);
        assert_eq!(pinned[1], [0.0; 2]);

        // Covered scalars have no drawn dimension under their own ID. Their pin
        // still fixes the view carrying the actual owner.
        let owner = plan
            .dimensions
            .iter_mut()
            .find(|d| d.view == "v:side")
            .unwrap();
        owner.id = "d:owner".into();
        plan.omitted.push(
            serde_json::from_value(json!({
            "id":"d:covered", "label":"covered scalar", "basis":"covered",
            "state":{"kind":"covered","by":"d:owner"}
            }))
            .unwrap(),
        );
        plan.spec = serde_json::from_value(json!({"dimensions":{
            "d:covered":{"place":{"pinned":true}}
        }}))
        .unwrap();
        let pinned = translations(&analysis, &plan, &bounds, PREFERRED, false).unwrap();
        assert_eq!(pinned[2], [0.0; 2]);
    }

    #[test]
    fn spare_paper_keeps_the_isometric_ink_a_view_gutter_from_a_table() {
        let (_, analysis) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/samples/editor-plate.step"),
        )
        .unwrap();
        let mut plan = crate::model::plan_recovered(&analysis, &Default::default()).unwrap();
        plan.views.retain(|v| v.id == "v:iso");
        plan.notes_block = None;
        plan.requirements.clear();
        let bounds = vec![[20.0, 80.0, 40.0, 100.0]];
        plan.views[0].at = [30.0, 90.0];
        // A table 7 mm right of the iso's ink; spare paper would move the iso up to 4 mm right.
        let rows = vec![vec!["A".to_string()]];
        plan.tables = vec![crate::tables::PlannedTable {
            sheet: 1,
            id: "t".into(),
            rows: rows.clone(),
            row_claims: Vec::new(),
            default: true,
            basis: String::new(),
            state: crate::items::State::Shown,
            placed: Some(crate::tables::lay(&rows, &[20.0], [47.0, 80.0])),
        }];
        let d = translations(&analysis, &plan, &bounds, PREFERRED, false).unwrap();
        assert!(d[0][0] > 0.5, "spare paper still moves the iso: {d:?}");
        let cells = plan.tables[0].placed.as_ref().unwrap().cells;
        assert!(cells[0] - (bounds[0][2] + d[0][0]) >= crate::layout::VIEW_GUTTER - 1e-3);
    }
}
