//! Inspection rings placed on settled label lanes, with the common leader predicates.
use super::{
    Placed,
    leaders::lines_meet,
    placement::{Frame, Page, boxes_overlap, crosses},
};
use crate::{
    characteristics::{Balloon, Characteristic, finding},
    model::{Analysis, Plan},
    pmi::Record,
};
type Box2 = [f64; 4];
type Line = [[f64; 2]; 2];
#[derive(Clone)]
struct Obstacle {
    id: String,
    bounds: Box2,
    lines: Vec<Line>,
    /// Dimension extension strokes may be crossed transversely, never followed.
    crossable: usize,
}
fn pad(b: Box2, p: f64) -> Box2 {
    [b[0] - p, b[1] - p, b[2] + p, b[3] + p]
}
fn centre(b: Box2) -> [f64; 2] {
    [(b[0] + b[2]) / 2., (b[1] + b[3]) / 2.]
}
fn frame(b: Box2) -> Vec<Line> {
    let [a, b, c, d] = b;
    vec![
        [[a, b], [c, b]],
        [[c, b], [c, d]],
        [[c, d], [a, d]],
        [[a, d], [a, b]],
    ]
}
fn obstacles(plan: &Plan, sheet: u32) -> Vec<Obstacle> {
    let mut out = Vec::new();
    for d in &plan.dimensions {
        if plan
            .views
            .iter()
            .find(|v| v.id == d.view)
            .is_none_or(|v| v.sheet != sheet)
        {
            continue;
        }
        if let Some(p) = &d.placed {
            let mut lines = p.lines.clone();
            lines.extend(p.arrows.iter().flat_map(|h| super::leaders::head(*h)));
            out.push(Obstacle {
                crossable: if matches!(d.kind.as_str(), "linear" | "angular") {
                    2
                } else {
                    0
                },
                id: d.id.clone(),
                bounds: p.label_box,
                lines,
            });
        }
    }
    for g in &plan.gdt {
        if plan
            .views
            .iter()
            .find(|v| v.id == g.view)
            .is_none_or(|v| v.sheet != sheet)
        {
            continue;
        }
        if let Some(p) = &g.placed {
            let mut lines = p.lines.clone();
            lines.extend(p.arrows.iter().flat_map(|h| super::leaders::head(*h)));
            lines.extend(&p.strokes);
            for fill in &p.fills {
                for k in 0..fill.len() {
                    lines.push([fill[k], fill[(k + 1) % fill.len()]]);
                }
            }
            out.push(Obstacle {
                crossable: 0,
                id: g.id.clone(),
                bounds: p.label_box,
                lines,
            });
        }
    }
    for b in &plan.balloons {
        if plan
            .views
            .iter()
            .find(|v| v.id == b.view)
            .is_some_and(|v| v.sheet == sheet)
        {
            let marker = super::callout_routes::balloon_obstacle(b);
            if let Some(p) = marker.placed {
                let mut lines = p.lines;
                lines.extend(p.arrows.into_iter().flat_map(super::leaders::head));
                out.push(Obstacle {
                    crossable: 0,
                    id: b.id.clone(),
                    bounds: p.label_box,
                    lines,
                });
            }
        }
    }
    fn table(out: &mut Vec<Obstacle>, t: &crate::tables::PlannedTable) {
        if let Some(p) = &t.placed {
            for p in std::iter::once(p)
                .filter(|p| p.blocks.is_empty())
                .chain(&p.blocks)
            {
                let mut lines = frame(p.cells);
                for &x in &p.columns {
                    lines.push([[x, p.cells[1]], [x, p.cells[3]]]);
                }
                for i in 1..p.cells_at.len() {
                    let y = p.cells[3] - i as f64 * crate::tables::ROW;
                    lines.push([[p.cells[0], y], [p.cells[2], y]]);
                }
                out.push(Obstacle {
                    crossable: 0,
                    id: t.id.clone(),
                    bounds: p.cells,
                    lines,
                });
            }
        }
    }
    for t in plan.tables.iter().filter(|t| t.sheet == sheet) {
        table(&mut out, t);
    }
    for t in plan
        .hole_table
        .iter()
        .chain(&plan.hole_tables)
        .filter(|t| t.sheet == sheet)
    {
        for b in &t.blocks {
            table(&mut out, b);
        }
    }
    if sheet == 1 {
        for n in &plan.notes {
            if let Some(p) = &n.placed {
                out.push(Obstacle {
                    crossable: 0,
                    id: n.id.clone(),
                    bounds: p.cells,
                    lines: frame(p.cells),
                });
            }
        }
        if let Some(b) = &plan.notes_block {
            out.push(Obstacle {
                crossable: 0,
                id: "general_notes".into(),
                bounds: [
                    b.cells[0],
                    b.cells[3] - crate::tables::ROW,
                    b.cells[2],
                    b.cells[3],
                ],
                lines: frame(b.cells),
            });
        }
        for r in &plan.requirements {
            if let Some(p) = &r.placed {
                out.push(Obstacle {
                    crossable: 0,
                    id: r.id.clone(),
                    bounds: p.label_box,
                    lines: p.strokes.clone(),
                });
            }
        }
        if let Some(t) = &plan.radius_table {
            table(&mut out, &t.table);
        }
        if let Some(b) = &plan.radius_notes_block {
            out.push(Obstacle {
                crossable: 0,
                id: "blend_radius_notes".into(),
                bounds: b.cells,
                lines: frame(b.cells),
            });
        }
    }
    // Section end markers are outside the view outline and are rendered after
    // the settled annotations. Reserve the same strokes and letters here.
    for cut in plan.views.iter().filter_map(|v| v.cut.as_ref()) {
        let Some(mark) = plan
            .views
            .iter()
            .find(|v| v.id == cut.marked_in && v.sheet == sheet)
        else {
            continue;
        };
        let Some(crate::output::Curve::Line(a, b)) = mark
            .line_work
            .iter()
            .find(|w| w.kind == "cutting_plane")
            .and_then(|w| w.curves.first())
        else {
            continue;
        };
        let (ink, letters) = crate::section::cutting_plane(a[0], b[0], a[1]);
        for (i, stroke) in ink.iter().enumerate() {
            if let Some(bounds) = crate::output::items_bbox(std::slice::from_ref(stroke)) {
                out.push(Obstacle {
                    id: format!("section:{}:{i}", cut.label),
                    bounds,
                    lines: frame(bounds),
                    crossable: 0,
                });
            }
        }
        let ink = super::font().text(&cut.label, crate::section::FONT_SIZE);
        for (i, at) in letters.into_iter().enumerate() {
            out.push(Obstacle {
                id: format!("section:{}:letter:{i}", cut.label),
                bounds: [
                    at[0] + ink.min[0],
                    at[1] + ink.min[1],
                    at[0] + ink.max[0],
                    at[1] + ink.max[1],
                ],
                lines: Vec::new(),
                crossable: 0,
            });
        }
    }
    for c in plan.captions.iter().filter(|c| c.sheet == sheet) {
        // Caption reservations are included by their complete box below when available.
        out.push(Obstacle {
            crossable: 0,
            id: c.id.clone(),
            bounds: c.ink,
            lines: Vec::new(),
        });
    }
    for b in plan.inspection_balloons.iter().filter(|b| b.sheet == sheet) {
        out.push(Obstacle {
            crossable: 0,
            id: b.id.clone(),
            bounds: b.bounds(),
            lines: b.lines.clone(),
        });
    }
    out
}
/// Finished tag relabelling must clear the same annotation ink as inspection routes.
pub(crate) fn tag_blocker(plan: &Plan, sheet: u32, own: &str, bounds: Box2) -> Option<String> {
    obstacles(plan, sheet).into_iter().find_map(|o| {
        (o.id != own
            && (boxes_overlap(bounds, o.bounds) || o.lines.iter().any(|l| crosses(*l, bounds))))
        .then_some(o.id)
    })
}

fn carrier_box(plan: &Plan, id: &str, obs: &[Obstacle]) -> Option<Box2> {
    if let Some(b) = obs.iter().find(|o| o.id == id) {
        return Some(b.bounds);
    }
    for t in plan.hole_table.iter().chain(&plan.hole_tables) {
        for (i, r) in t.rows.iter().enumerate() {
            if id == format!("{}/{}", t.id, r.key()) {
                let block = t.blocks.iter().find(|b| {
                    b.rows
                        .iter()
                        .skip(1)
                        .any(|r| r.first() == Some(&t.rows[i].tag))
                })?;
                let row = block
                    .rows
                    .iter()
                    .position(|r| r.first() == Some(&t.rows[i].tag))?;
                let p = block.placed.as_ref()?;
                let y = p.cells[3] - row as f64 * crate::tables::ROW;
                return Some([p.cells[0], y - crate::tables::ROW, p.cells[2], y]);
            }
        }
    }
    None
}
fn boundary(b: Box2, toward: [f64; 2]) -> [f64; 2] {
    let c = centre(b);
    let d = [toward[0] - c[0], toward[1] - c[1]];
    let t = (0..2)
        .filter(|&k| d[k].abs() > 1e-9)
        .map(|k| ((b[k + 2] - b[k]) / 2.) / d[k].abs())
        .fold(f64::INFINITY, f64::min);
    if !t.is_finite() {
        return c;
    }
    [c[0] + d[0] * t, c[1] + d[1] * t]
}
fn shortened([a, b]: Line) -> Line {
    let l = (b[0] - a[0]).hypot(b[1] - a[1]).max(1e-9);
    let f = (0.3 / l).min(0.49);
    [[a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], b]
}
#[derive(Default)]
struct Routing<'a> {
    occurrence: Option<&'a super::footprints::Footprints>,
    bends: bool,
    /// A connected number group starts on an existing ring's actual rim.
    circular: bool,
}
fn candidate(
    page: &Page<'_>,
    obs: &[Obstacle],
    target: Box2,
    carrier: (&str, &str),
    at: [f64; 2],
    radius: f64,
    routing: Routing<'_>,
) -> Option<Balloon> {
    let footprints = routing.occurrence;
    let (own, view) = carrier;
    let bounds = [
        at[0] - radius,
        at[1] - radius,
        at[0] + radius,
        at[1] + radius,
    ];
    let mut p = Placed {
        label_box: pad(bounds, 0.8),
        label_at: at,
        rotation: 0.,
        lines: Vec::new(),
        arrows: Vec::new(),
    };
    if !page.fits(&p, view.trim_start_matches("v:"))
        || obs.iter().any(|o| {
            boxes_overlap(p.label_box, o.bounds) || o.lines.iter().any(|l| crosses(*l, p.label_box))
        })
    {
        return None;
    }
    let leaves = |b: Box2, a: [f64; 2], z: [f64; 2]| {
        let d = [z[0] - a[0], z[1] - a[1]];
        let len = d[0].hypot(d[1]);
        ((a[0] - b[0]).abs() < 1e-6 && d[0] < -0.2 * len)
            || ((a[0] - b[2]).abs() < 1e-6 && d[0] > 0.2 * len)
            || ((a[1] - b[1]).abs() < 1e-6 && d[1] < -0.2 * len)
            || ((a[1] - b[3]).abs() < 1e-6 && d[1] > 0.2 * len)
    };
    let circle_distance = |[a, z]: Line| {
        let d = [z[0] - a[0], z[1] - a[1]];
        let t = (((at[0] - a[0]) * d[0] + (at[1] - a[1]) * d[1])
            / (d[0] * d[0] + d[1] * d[1]).max(1e-12))
        .clamp(0., 1.);
        (a[0] + t * d[0] - at[0]).hypot(a[1] + t * d[1] - at[1])
    };
    let clear = |route: &[Line]| {
        if route.is_empty() {
            return false;
        }
        if footprints.is_none() && !leaves(target, route[0][0], route[0][1]) {
            return false;
        }
        for (i, line) in route.iter().copied().enumerate() {
            let checked = if i == 0 { shortened(line) } else { line };
            let ring_checked = if i + 1 == route.len() {
                let [a, b] = shortened([line[1], line[0]]);
                [b, a]
            } else {
                line
            };
            if circle_distance(ring_checked) < radius + 0.05 {
                return false;
            }
            if obs.iter().any(|o| {
                // A ring grown from another ring may start on that ring's rim, but a bend after
                // its first stroke keeps the same clearance from it as from any other ring.
                (if o.id == own && (i == 0 || !routing.circular) {
                    crosses(checked, pad(o.bounds, -0.05))
                } else {
                    crosses(line, pad(o.bounds, 0.5))
                }) || o.lines.iter().enumerate().any(|(i, l)| {
                    let line = if o.id == own { checked } else { line };
                    if i < o.crossable {
                        super::leaders::segments_along(line, *l)
                    } else {
                        lines_meet(line, *l)
                    }
                })
            }) || footprints.is_some_and(|f| f.crosses_feature(checked))
            {
                return false;
            }
        }
        let q = Placed {
            lines: route.to_vec(),
            ..p.clone()
        };
        // Prefer a connection outside all views. Interior carriers can still use
        // their own view's established annotation lanes in the routed pass.
        page.fits(
            &q,
            if footprints.is_some() || routing.bends {
                view.trim_start_matches("v:")
            } else {
                ""
            },
        )
    };
    let segments = |points: Vec<[f64; 2]>| -> Vec<Line> {
        points
            .windows(2)
            .filter(|w| (w[0][0] - w[1][0]).hypot(w[0][1] - w[1][1]) > 0.01)
            .map(|w| [w[0], w[1]])
            .collect()
    };
    let start = if routing.circular {
        let mid = centre(target);
        let d = [at[0] - mid[0], at[1] - mid[1]];
        if d[0].abs() >= d[1].abs() {
            [if d[0] > 0. { target[2] } else { target[0] }, mid[1]]
        } else {
            [mid[0], if d[1] > 0. { target[3] } else { target[1] }]
        }
    } else if footprints.is_some() {
        let mid = centre(target);
        let dx = at[0] - mid[0];
        let dy = at[1] - mid[1];
        let len = dx.hypot(dy).max(1e-12);
        let rr = (target[2] - target[0]) / 2.;
        [mid[0] + dx / len * rr, mid[1] + dy / len * rr]
    } else {
        boundary(target, at)
    };
    let dx = start[0] - at[0];
    let dy = start[1] - at[1];
    let length = dx.hypot(dy);
    if length <= radius + 0.5 {
        return None;
    }
    let end = [at[0] + dx / length * radius, at[1] + dy / length * radius];
    let mut selected = clear(&[[start, end]]).then_some(vec![[start, end]]);
    if selected.is_none() && routing.bends {
        let ports = |b: Box2| {
            [0.5, 0.25, 0.75, 0.0, 1.0]
                .into_iter()
                .flat_map(|f| {
                    let x = b[0] + f * (b[2] - b[0]);
                    let y = b[1] + f * (b[3] - b[1]);
                    [[b[0], y], [b[2], y], [x, b[1]], [x, b[3]]]
                })
                .collect::<Vec<_>>()
        };
        let mut starts = if routing.circular {
            let mid = centre(target);
            vec![
                [target[0], mid[1]],
                [target[2], mid[1]],
                [mid[0], target[1]],
                [mid[0], target[3]],
            ]
        } else {
            ports(target)
        };
        starts.sort_by(|a, b| {
            (a[0] - at[0])
                .hypot(a[1] - at[1])
                .total_cmp(&(b[0] - at[0]).hypot(b[1] - at[1]))
        });
        let ends = {
            let c = centre(bounds);
            vec![
                [bounds[0], c[1]],
                [bounds[2], c[1]],
                [c[0], bounds[1]],
                [c[0], bounds[3]],
            ]
        };
        let mid = centre(target);
        let lanes = |axis: usize| {
            let mut values: Vec<_> = obs
                .iter()
                .flat_map(|o| {
                    std::iter::once(o.bounds[axis])
                        .chain(std::iter::once(o.bounds[axis + 2]))
                        .chain(o.lines.iter().flatten().map(move |p| p[axis]))
                })
                .flat_map(|v| [v - 1.5, v + 1.5])
                .filter(|v| (v - mid[axis]).abs() <= 70.)
                .collect();
            values.sort_by(f64::total_cmp);
            values.dedup_by(|a, b| (*a - *b).abs() < 0.1);
            values.sort_by(|a, b| (a - mid[axis]).abs().total_cmp(&(b - mid[axis]).abs()));
            values.truncate(if routing.circular { 48 } else { 24 });
            values
        };
        let xs = lanes(0);
        let ys = lanes(1);
        'search: for a in starts {
            // A corner port can leave a narrow row diagonally without touching
            // the neighbouring ring. The centre-to-centre ray may instead hit
            // an interior grid edge, and orthogonal bends can graze that ring.
            let d = [a[0] - at[0], a[1] - at[1]];
            let length = d[0].hypot(d[1]);
            let z = [
                at[0] + d[0] / length * radius,
                at[1] + d[1] / length * radius,
            ];
            if length > radius + 0.5 && clear(&[[a, z]]) {
                selected = Some(vec![[a, z]]);
                break 'search;
            }
            for &z in &ends {
                for bend in [[a[0], z[1]], [z[0], a[1]]] {
                    let route = segments(vec![a, bend, z]);
                    if clear(&route) {
                        selected = Some(route);
                        break 'search;
                    }
                }
                for &x in &xs {
                    let route = segments(vec![a, [x, a[1]], [x, z[1]], z]);
                    if clear(&route) {
                        selected = Some(route);
                        break 'search;
                    }
                }
                for &y in &ys {
                    let route = segments(vec![a, [a[0], y], [z[0], y], z]);
                    if clear(&route) {
                        selected = Some(route);
                        break 'search;
                    }
                }
                if routing.circular {
                    // Leave a ring normally, pass the neighbouring frame/section
                    // letter, then return to the next ring through a clear lane.
                    for &x in &xs {
                        for &y in &ys {
                            for points in [
                                vec![a, [x, a[1]], [x, y], [z[0], y], z],
                                vec![a, [a[0], y], [x, y], [x, z[1]], z],
                            ] {
                                let route = segments(points);
                                if clear(&route) {
                                    selected = Some(route);
                                    break 'search;
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    p.lines = selected?;
    Some(Balloon {
        state: crate::items::State::Shown,
        default: true,
        basis: String::new(),
        id: String::new(),
        source: String::new(),
        number: String::new(),
        item: String::new(),
        sheet: 0,
        view: None,
        at,
        radius,
        lines: p.lines,
    })
}
/// Keep the complete glyph box inside the ring with 0.35 mm clear of its inner edge.
/// A character-count estimate wastes the narrow lanes beside compact datum frames.
fn number_radius(number: &str) -> f64 {
    let ink = super::font().text(number, super::TEXT);
    (ink.max[0].hypot(ink.max[1]) + 0.6).max(3.0)
}
pub(crate) fn place(analysis: &Analysis, plan: &mut Plan, r: &Record, c: &mut Characteristic) {
    let Some(item) = c.drawn_item.clone() else {
        return;
    };
    let sheet = c.sheet.unwrap_or(1);
    let Some(paper) = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .find(|s| s.number == sheet)
    else {
        return;
    };
    let page = Page::new(analysis, paper, &plan.views);
    let obs = obstacles(plan, sheet);
    let Some(target) = carrier_box(plan, &item, &obs) else {
        finding(
            plan,
            c,
            "characteristic_balloon_missing",
            "has no locatable drawn carrier for its balloon",
        );
        return;
    };
    let setting = plan
        .spec
        .balloons
        .items
        .get(&c.source)
        .or_else(|| {
            c.item
                .as_ref()
                .and_then(|i| plan.spec.balloons.items.get(i))
        })
        .cloned()
        .unwrap_or_default();
    let mut targets = vec![(target, Some(item.clone()))];
    // A counted callout becomes individual row statements in a hole schedule.
    // Repeat its file-owned number beside every row that states it, not just
    // the first row carrying the shared semantic measurement for coverage.
    if let Some(table) = plan
        .hole_table
        .iter()
        .chain(&plan.hole_tables)
        .find(|t| item.starts_with(&format!("{}/", t.id)))
    {
        let members: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| Some(&d.id) == c.item.as_ref())
            .flat_map(|d| &d.measurements)
            .filter(|m| m.parameter == "bore.diameter")
            .map(|m| &m.feature)
            .collect();
        for row in &table.rows {
            let id = format!("{}/{}", table.id, row.key());
            if id != item
                && members.contains(&&row.feature)
                && let Some(bounds) = carrier_box(plan, &id, &obs)
            {
                targets.push((bounds, Some(id)));
            }
        }
    }
    let mut occurrence_view = None;
    let mut occurrence_frame = None;
    // Geometric cylinders or whole recognised holes prove physical repeated occurrences.
    // Never count a location dimension's two witness stations as occurrences.
    if plan.spec.balloons.occurrences {
        let mut cylinders: Vec<([f64; 3], [f64; 3], f64)> = r
            .geometry
            .as_ref()
            .into_iter()
            .flat_map(|g| &g.cylinders)
            .map(|cy| (cy.midpoint(), cy.direction, cy.radius))
            .collect();
        if cylinders.is_empty()
            && matches!(
                r.category,
                crate::pmi::Category::Datum | crate::pmi::Category::GeometricTolerance
            )
        {
            cylinders = crate::bridge::holes::read(analysis)
                .into_iter()
                .filter_map(|(_, h)| {
                    let h = h.ok()?;
                    (h.feature.faces.iter().all(|f| r.faces.contains(f))).then_some((
                        h.at,
                        h.direction,
                        h.diameter / 2.,
                    ))
                })
                .collect();
        }
        if cylinders.len() > 1 {
            let mut cameras: Vec<_> = plan
                .views
                .iter()
                .filter(|v| {
                    v.sheet == sheet && v.cut.is_none() && v.detail.is_none() && v.id != "v:iso"
                })
                .collect();
            cameras.sort_by_key(|v| c.view.as_ref() != Some(&v.id));
            if let Some(view) = cameras.into_iter().find(|v| {
                cylinders.iter().all(|(_, axis, _)| {
                    (0..3).map(|k| axis[k] * v.direction[k]).sum::<f64>().abs() > 0.99
                })
            }) {
                let f = Frame::new(analysis, view);
                let mut sites: Vec<[f64; 2]> = Vec::new();
                for (at, _, rad) in cylinders {
                    let p = f.page(at);
                    let rr = rad * view.scale;
                    if !sites
                        .iter()
                        .any(|q| (q[0] - p[0]).hypot(q[1] - p[1]) < 0.01)
                    {
                        sites.push(p);
                        targets.push(([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr], None));
                    }
                }
                occurrence_view = Some(view.id.clone());
                occurrence_frame = Some(f);
            }
        }
    }
    let footprints = occurrence_frame.as_ref().map(|f| {
        super::footprints::Footprints::new(
            analysis,
            f,
            occurrence_view
                .as_deref()
                .unwrap_or("")
                .trim_start_matches("v:"),
        )
    });
    let radius = number_radius(&c.number);
    let mut balloons = Vec::new();
    for (i, (t, row_item)) in targets.into_iter().enumerate() {
        let occurrence = row_item.is_none();
        let target_item = row_item.as_deref().unwrap_or(&item);
        let parent = target_item.rsplit_once('/').map_or(target_item, |p| p.0);
        let own = obs
            .iter()
            .find(|o| {
                o.id == target_item
                    || ((o.id == parent || o.id.starts_with(&format!("{parent}/")))
                        && o.bounds[0] <= t[0]
                        && o.bounds[1] <= t[1]
                        && o.bounds[2] >= t[2]
                        && o.bounds[3] >= t[3])
            })
            .map_or(parent, |o| o.id.as_str());
        let id = if i == 0 {
            format!("inspection:{}", c.source)
        } else if let Some(row) = &row_item {
            format!(
                "inspection:{}:row:{}",
                c.source,
                row.rsplit('/').next().unwrap_or(row)
            )
        } else {
            format!("inspection:{}:occurrence:{i}", c.source)
        };
        c.balloon_ids.push(id.clone());
        if setting.suppress {
            continue;
        }
        let mid = centre(t);
        let mut places = Vec::new();
        if setting.position.is_none() {
            // A last row can use the open bottom edge of its table, leaving the
            // narrow side column for the preceding rows' repeated numbers.
            if let Some(block) = plan
                .hole_table
                .iter()
                .chain(&plan.hole_tables)
                .flat_map(|table| &table.blocks)
                .filter_map(|block| block.placed.as_ref())
                .find(|p| (p.cells[1] - t[1]).abs() < 0.01 && p.cells[0] == t[0])
            {
                places.push([
                    (block.columns[0] + block.columns[1]) / 2.,
                    t[1] - radius - 1.5,
                ]);
            }
        }
        if let Some(d) = setting.position {
            places.push([mid[0] + d[0], mid[1] + d[1]]);
        } else {
            for reach in [1.5, 4.5, 8.0, 12.0, 18.0, 26.0, 38.0, 52.0] {
                for f in [0.5, 0.25, 0.75, 0., 1., -0.25, 1.25, -0.5, 1.5, -1., 2.] {
                    places.extend([
                        [t[2] + radius + reach, t[1] + (t[3] - t[1]) * f],
                        [t[0] - radius - reach, t[1] + (t[3] - t[1]) * f],
                        [t[0] + (t[2] - t[0]) * f, t[3] + radius + reach],
                        [t[0] + (t[2] - t[0]) * f, t[1] - radius - reach],
                    ]);
                }
            }
        }
        if setting.position.is_none() {
            // Shared callouts may carry many distinct requirement numbers. Search
            // diagonal sites too, without sharing a leader port or crossing a view.
            for reach in [8., 12., 18., 26., 38., 52.] {
                for dx in [-1., -0.5, 0.5, 1.] {
                    for dy in [-1., -0.5, 0.5, 1.] {
                        places.push([mid[0] + dx * reach, mid[1] + dy * reach]);
                    }
                }
            }
        }
        if setting.position.is_none()
            && let Some(&(_, o)) = page
                .views()
                .iter()
                .find(|(id, b)| Some(*id) == c.view.as_deref() && boxes_overlap(*b, t))
        {
            // Last, a carrier deep inside a large view may be further from every edge than
            // the reaches above. Its ring can still stand just outside that view, square to the
            // carrier, its leader crossing the view's interior as interior callouts' do.
            for gap in [3.0, 8.0, 14.0] {
                for f in [0.5, 0.25, 0.75, 0., 1.] {
                    let along = [t[0] + (t[2] - t[0]) * f, t[1] + (t[3] - t[1]) * f];
                    places.extend([
                        [o[0] - radius - gap, along[1]],
                        [o[2] + radius + gap, along[1]],
                        [along[0], o[1] - radius - gap],
                        [along[0], o[3] + radius + gap],
                    ]);
                }
            }
        }
        let mut all = obs.clone();
        all.extend(balloons.iter().map(|b: &Balloon| Obstacle {
            crossable: 0,
            id: b.id.clone(),
            bounds: b.bounds(),
            lines: b.lines.clone(),
        }));
        let locate = |routed| {
            places.iter().find_map(|&at| {
                candidate(
                    &page,
                    &all,
                    t,
                    (
                        if occurrence { "" } else { own },
                        if !occurrence {
                            c.view.as_deref().unwrap_or("")
                        } else {
                            occurrence_view.as_deref().unwrap_or("")
                        },
                    ),
                    at,
                    radius,
                    Routing {
                        occurrence: if occurrence {
                            footprints.as_ref()
                        } else {
                            None
                        },
                        bends: routed,
                        circular: false,
                    },
                )
            })
        };
        let placed = locate(false);
        // Several requirements can be conveyed by one compound callout. If its
        // only exit is already occupied by a number leader, grow a connected
        // group of rings from that carrier's existing ring, without duplicate
        // strokes through the narrow exit or borrowing another item's leader.
        let placed = placed
            .or_else(|| {
                if setting.position.is_some() || occurrence {
                    return None;
                }
                plan.inspection_balloons
                    .iter()
                    .filter(|b| b.item == target_item)
                    .find_map(|previous| {
                        let bounds = previous.bounds();
                        [4.5, 8., 12., 18., 26., 38.].into_iter().find_map(|reach| {
                            let mid = centre(bounds);
                            [0., -0.5, 0.5, -1., 1.]
                                .into_iter()
                                .flat_map(|f| {
                                    [
                                        [bounds[2] + radius + reach, mid[1] + f * reach],
                                        [bounds[0] - radius - reach, mid[1] + f * reach],
                                        [mid[0] + f * reach, bounds[3] + radius + reach],
                                        [mid[0] + f * reach, bounds[1] - radius - reach],
                                    ]
                                })
                                .find_map(|at| {
                                    candidate(
                                        &page,
                                        &all,
                                        bounds,
                                        (&previous.id, c.view.as_deref().unwrap_or("")),
                                        at,
                                        radius,
                                        Routing {
                                            occurrence: None,
                                            bends: true,
                                            circular: true,
                                        },
                                    )
                                })
                        })
                    })
            })
            .or_else(|| locate(true));
        if let Some(mut b) = placed {
            b.id = id;
            b.source = c.source.clone();
            b.number = c.number.clone();
            b.item = target_item.to_string();
            b.sheet = sheet;
            b.view = if !occurrence {
                c.view.clone()
            } else {
                occurrence_view.clone()
            };
            b.default = setting.position.is_none();
            b.basis = format!(
                "File-owned characteristic {} from {}; connected to its drawn carrier",
                c.number, c.source
            );
            balloons.push(b);
        }
    }
    if balloons.len() != c.balloon_ids.len() {
        finding(
            plan,
            c,
            "characteristic_balloon_missing",
            if setting.suppress {
                "has a suppressed inspection balloon"
            } else {
                "has an inspection balloon with no clear placement"
            },
        );
    }
    plan.inspection_balloons.extend(balloons);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_number_ring_fits_the_enclosed_lane_beside_a_datum() {
        let (_, a) = crate::model::analyse_native_bytes(
            "datum.step",
            include_bytes!("../../tests/fixtures/parts/editor_pmi.step"),
        )
        .unwrap();
        let plan = crate::model::plan_recovered(&a, &crate::model::Spec::default()).unwrap();
        let page = Page::new(&a, &plan.sheet, &[]);
        let target = [64., 71.5, 70., 77.5];
        let obs = vec![
            Obstacle {
                crossable: 0,
                id: "datum".into(),
                bounds: target,
                lines: frame(target),
            },
            // The view below and the dimension's witness strokes enclose a 9 mm lane.
            Obstacle {
                crossable: 0,
                id: "view".into(),
                bounds: [32., 20., 100., 70.2],
                lines: vec![],
            },
            Obstacle {
                crossable: 0,
                id: "dimension".into(),
                bounds: [60., 80.2, 72., 82.4],
                lines: vec![
                    [[32., 70.2], [32., 81.2]],
                    [[100., 20.], [100., 81.2]],
                    [[32., 79.2], [100., 79.2]],
                ],
            },
        ];
        let b = candidate(
            &page,
            &obs,
            target,
            ("datum", ""),
            [78., 74.5],
            number_radius("122"),
            Routing::default(),
        )
        .unwrap();
        assert_eq!(b.lines.len(), 1);
        let ink = super::super::font().text("122", super::super::TEXT);
        assert!(ink.max[0].hypot(ink.max[1]) < b.radius - 0.5);
        // Departing tangentially along the datum's edge would cross its own text.
        assert!(b.lines[0][1][0] > b.lines[0][0][0]);
    }
}
