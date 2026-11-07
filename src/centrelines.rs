//! ISO 128 chain-line furniture, resolved in page mm before annotation placement.
use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::bridge::holes;
use crate::dims::{Placed, PlannedDimension};
use crate::model::{Analysis, PlannedView};
use crate::output::{Curve, P};
use haecceity::hlr::View;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LineWork {
    pub id: String,
    /// `cross`, `axis`, `pitch_circle` or `cutting_plane`.
    pub kind: String,
    pub curves: Vec<Curve>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub centre: Option<P>,
    #[serde(default)]
    pub radial: bool,
    #[serde(default)]
    pub global_axis: bool,
    /// How far an axis runs past its feature at each end, mm: what [`trim`] may shorten.
    /// Planning state only, not part of the drawing's identity.
    #[serde(skip)]
    pub overrun: f64,
}

impl PartialEq for LineWork {
    fn eq(&self, o: &Self) -> bool {
        self.id == o.id
            && self.kind == o.kind
            && self.curves == o.curves
            && self.centre == o.centre
            && self.radial == o.radial
            && self.global_axis == o.global_axis
    }
}

pub fn overrun(radius: f64) -> f64 {
    radius.clamp(1.0, 3.0).max(radius * 0.05)
}

fn page(a: &Analysis, v: &PlannedView, p: [f64; 3]) -> P {
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = View::new(v.direction, v.up).expect("planned view");
    let centre = crate::detail::crop_extent(a, v).1;
    let q = view.map(p);
    [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k]))
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    (0..3).map(|k| a[k] * b[k]).sum()
}

fn removed(
    cut: Option<&crate::model::Cut>,
    at: [f64; 3],
    d: [f64; 3],
    length: f64,
    diameter: f64,
) -> bool {
    cut.is_some_and(|c| {
        let axial = dot(d, c.normal).abs().min(1.);
        let support = (length * axial + diameter * (1. - axial * axial).sqrt()) / 2.;
        dot([0, 1, 2].map(|k| at[k] - c.origin[k]), c.normal) < -support - 1e-6
    })
}

fn cross(id: String, c: P, radius: f64, radial: Option<P>) -> LineWork {
    let u = radial.unwrap_or([1.0, 0.0]);
    let r = radius + overrun(radius);
    let curves = [u, [-u[1], u[0]]].map(|d| {
        Curve::Line(
            [c[0] - r * d[0], c[1] - r * d[1]],
            [c[0] + r * d[0], c[1] + r * d[1]],
        )
    });
    LineWork {
        id,
        kind: "cross".into(),
        curves: curves.into(),
        centre: Some(c),
        radial: radial.is_some(),
        global_axis: false,
        overrun: 0.,
    }
}

fn end_on(v: &PlannedView, d: [f64; 3]) -> bool {
    let k = (0..3)
        .max_by(|&a, &b| d[a].abs().total_cmp(&d[b].abs()).then(b.cmp(&a)))
        // Invariant: The iterator is over the fixed three coordinate axes.
        .expect("three components");
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    View::new(v.direction, v.up).unwrap().toward[k].abs() > 1. - 1e-6
}

fn end_cross(
    v: &PlannedView,
    id: String,
    c: P,
    d: [f64; 3],
    r: f64,
    radial: Option<P>,
) -> LineWork {
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = View::new(v.direction, v.up).unwrap();
    let p = view.map(d);
    let n = p[0].hypot(p[1]);
    let mut w = cross(id, c, r, radial);
    if n > 1e-6 && radial.is_none() {
        let u = p.map(|x| x / n);
        let minor = r * dot(d, view.toward).abs().min(1.);
        w.curves = [(u, minor), ([-u[1], u[0]], r)]
            .map(|(d, r)| {
                let r = r + overrun(r);
                Curve::Line(
                    [c[0] - r * d[0], c[1] - r * d[1]],
                    [c[0] + r * d[0], c[1] + r * d[1]],
                )
            })
            .into();
    }
    w
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn axis(
    a: &Analysis,
    v: &PlannedView,
    id: String,
    at: [f64; 3],
    direction: [f64; 3],
    length: f64,
    diameter: f64,
    global: bool,
) -> Option<LineWork> {
    let view = View::new(v.direction, v.up)?;
    if dot(direction, view.toward).abs() > 1.0 - 1e-6 {
        return None;
    }
    let (p, q) = (
        page(a, v, [0, 1, 2].map(|k| at[k] - direction[k] * length / 2.0)),
        page(a, v, [0, 1, 2].map(|k| at[k] + direction[k] * length / 2.0)),
    );
    let d = [q[0] - p[0], q[1] - p[1]];
    let n = d[0].hypot(d[1]);
    if n < 1e-6 {
        return None;
    }
    let extra = if global {
        5.0
    } else {
        overrun(diameter * v.scale / 2.0)
    };
    let d = d.map(|x| x * extra / n);
    Some(LineWork {
        id,
        kind: "axis".into(),
        curves: vec![Curve::Line(
            [p[0] - d[0], p[1] - d[1]],
            [q[0] + d[0], q[1] + d[1]],
        )],
        centre: None,
        radial: false,
        global_axis: global,
        overrun: extra,
    })
}

/// Populate every shown view; dimensional omissions never affect furniture.
pub(crate) fn plan(a: &Analysis, views: &mut [PlannedView]) {
    let frame = holes::frame(a);
    let holes: Vec<_> = holes::read(a)
        .into_iter()
        .filter_map(|(_, h)| h.ok())
        .collect();
    let bands: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .filter_map(|f| crate::lint::turned::band(&frame, f).ok().map(|b| (f, b)))
        .collect();
    let mut round = holes::bosses(a);
    round.extend(bands.iter().map(|(_, b)| {
        let mut direction = [0.; 3];
        direction[b.axis] = 1.;
        holes::Axis {
            at: b.on,
            direction,
        }
    }));
    let slot_ends = crate::dims::located_end_centres(a);
    let [lo, hi] = a.envelope;
    for v in views.iter_mut() {
        v.line_work.clear();
        if (v.id == "v:iso" || v.detail.is_some())
            && v.line_style.centre_marks != Some(true)
            && v.line_style.centre_lines != Some(true)
        {
            continue;
        }
        // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
        let view = View::new(v.direction, v.up).expect("planned view");
        for s in crate::springs::read(a) {
            if let Some(w) = axis(
                a,
                v,
                format!("spring_axis_{}", s.feature.id),
                s.at((s.start + s.end) / 2.),
                s.axis,
                s.end - s.start,
                s.outer,
                false,
            ) {
                v.line_work.push(w);
            }
        }
        // The hole's full span: from its opening (`at`) along its direction through any
        // counterbore or spotface and then the bore. None when a depth is missing; then no side
        // axis is drawn, since running one across the part would read as THRU (ADR R1-6).
        let hole_span = |h: &holes::Hole<'_>| -> Option<f64> {
            let recess = h.recesses().map(|r| r.depth).sum::<Option<f64>>()?;
            Some(recess + h.depth?)
        };
        let hole_axis = |h: &holes::Hole<'_>, length: f64| {
            [0, 1, 2].map(|k| h.at[k] + h.direction[k] * length / 2.)
        };
        let hole_diameter = |h: &holes::Hole<'_>| h.opening_diameter();
        let hole_removed = |h: &holes::Hole<'_>| {
            // Only whether the cut takes the hole away: the envelope span stands in for a
            // missing depth here, never for the drawn axis.
            let (at, length) = match hole_span(h) {
                Some(l) => (hole_axis(h, l), l),
                None => (
                    h.feature.fingerprint.position,
                    (0..3).map(|k| (hi[k] - lo[k]) * h.direction[k].abs()).sum(),
                ),
            };
            removed(v.cut.as_ref(), at, h.direction, length, hole_diameter(h))
        };
        let mut radial = BTreeMap::new();
        for p in a
            .recognition
            .iter()
            .flat_map(|r| &r.patterns)
            .filter(|p| p.arrangement == "bolt_circle")
        {
            if p.members.len() < 5
                && holes::established_with(p, &holes.iter().collect::<Vec<_>>(), &round).is_err()
            {
                continue;
            }
            let Some(h) = holes.iter().find(|h| p.members.contains(&h.feature.id)) else {
                continue;
            };
            if dot(h.direction, view.toward).abs() < 1.0 - 1e-6 {
                continue;
            }
            let Some(c) =
                holes::point(p.parameters.get("center")).map(|p| holes::to_part(&frame, p))
            else {
                continue;
            };
            let Some(diameter) = p.parameters.get("diameter").and_then(|x| x.as_f64()) else {
                continue;
            };
            if diameter <= 0.0 {
                continue;
            }
            let c = page(a, v, c);
            let r = diameter * v.scale / 2.0;
            // Require every member to lie on the recorded circle, rather than inventing one.
            let members: Vec<_> = p
                .members
                .iter()
                .filter_map(|id| holes.iter().find(|h| &h.feature.id == id))
                .collect();
            if members.len() != p.members.len()
                || members.iter().any(|h| hole_removed(h))
                || members.iter().any(|h| {
                    let q = page(a, v, h.at);
                    ((q[0] - c[0]).hypot(q[1] - c[1]) - r).abs() > 0.05
                })
            {
                continue;
            }
            for h in members {
                let q = page(a, v, h.at);
                radial.insert(
                    h.feature.id.as_str(),
                    [(q[0] - c[0]) / r, (q[1] - c[1]) / r],
                );
            }
            v.line_work.push(LineWork {
                id: format!("ink:{}:{}", v.id, p.id),
                kind: "pitch_circle".into(),
                curves: vec![Curve::Conic {
                    centre: c,
                    radii: [r, r],
                    x_axis: [1., 0.],
                    y_axis: [0., 1.],
                    t0: 0.,
                    t1: std::f64::consts::TAU,
                }],
                centre: None,
                radial: false,
                global_axis: false,
                overrun: 0.,
            });
        }
        // Native recognition may publish the members without their bolt-circle record.
        // Five equal, coplanar bores must all fit one circle; a rectangular grid is not evidence.
        let mut groups: BTreeMap<Vec<i64>, Vec<&holes::Hole<'_>>> = BTreeMap::new();
        for h in &holes {
            if dot(h.direction, view.toward).abs() < 1.0 - 1e-6
                || radial.contains_key(h.feature.id.as_str())
            {
                continue;
            }
            let mut key = h.requirement();
            key.push(holes::size_key(dot(h.at, view.toward)));
            groups.entry(key).or_default().push(h);
        }
        for members in groups.values().filter(|g| g.len() >= 5) {
            if members.iter().any(|h| hole_removed(h)) {
                continue;
            }
            let points: Vec<_> = members.iter().map(|h| page(a, v, h.at)).collect();
            if let Some((c, r)) = fitted_circle(&points, v.scale) {
                for (h, q) in members.iter().zip(&points) {
                    radial.insert(
                        h.feature.id.as_str(),
                        [(q[0] - c[0]) / r, (q[1] - c[1]) / r],
                    );
                }
                v.line_work.push(LineWork {
                    id: format!("ink:{}:pitch:{}", v.id, members[0].feature.id),
                    kind: "pitch_circle".into(),
                    curves: vec![Curve::Conic {
                        centre: c,
                        radii: [r, r],
                        x_axis: [1., 0.],
                        y_axis: [0., 1.],
                        t0: 0.,
                        t1: std::f64::consts::TAU,
                    }],
                    centre: None,
                    radial: false,
                    global_axis: false,
                    overrun: 0.,
                });
            }
        }
        for h in &holes {
            if hole_removed(h) {
                continue;
            }
            let id = format!("ink:{}:{}", v.id, h.feature.id);
            let radius = hole_diameter(h) * v.scale / 2.;
            if end_on(v, h.direction) {
                v.line_work.push(end_cross(
                    v,
                    id,
                    page(a, v, h.at),
                    h.direction,
                    radius,
                    radial.get(h.feature.id.as_str()).copied(),
                ));
            } else if let Some(length) = hole_span(h) {
                let at = hole_axis(h, length);
                if let Some(w) = axis(a, v, id, at, h.direction, length, h.diameter, false) {
                    v.line_work.push(w);
                }
            }
        }
        // A slot dimensioned by its end centres: a centre mark at each, where its dimensions end.
        for (f, deep, ends, r) in &slot_ends {
            let mut d = [0.; 3];
            d[*deep] = 1.;
            let length = hi[*deep] - lo[*deep];
            if !end_on(v, d)
                || ends
                    .iter()
                    .any(|c| removed(v.cut.as_ref(), *c, d, length, 2. * r))
            {
                continue;
            }
            for (k, c) in ends.iter().enumerate() {
                v.line_work.push(cross(
                    format!("ink:{}:{}:end{k}", v.id, f.id),
                    page(a, v, *c),
                    r * v.scale,
                    None,
                ));
            }
        }
        for f in a.features.iter().filter(|f| f.kind == "double_d_bore") {
            let p = &f.parameters;
            if let (Some(at), Some(d), Some(diameter)) = (
                holes::point(p.get("location")),
                holes::point(p.get("axis")),
                p.get("major_diameter").and_then(|x| x.as_f64()),
            ) {
                let (at, d) = (holes::to_part(&frame, at), holes::dir_to_part(&frame, d));
                // As for holes: a missing depth decides removal only, never the drawn axis.
                let depth = p.get("depth").and_then(|x| x.as_f64());
                let length =
                    depth.unwrap_or_else(|| (0..3).map(|k| (hi[k] - lo[k]) * d[k].abs()).sum());
                let centre = f.fingerprint.position;
                if removed(v.cut.as_ref(), centre, d, length, diameter) {
                    continue;
                }
                let id = format!("ink:{}:{}", v.id, f.id);
                if end_on(v, d) {
                    v.line_work.push(end_cross(
                        v,
                        id,
                        page(a, v, at),
                        d,
                        diameter * v.scale / 2.,
                        None,
                    ));
                } else if let Some(w) =
                    depth.and_then(|_| axis(a, v, id, centre, d, length, diameter, false))
                {
                    v.line_work.push(w);
                }
            }
        }
        for (f, b) in &bands {
            let mut direction = [0.; 3];
            direction[b.axis] = 1.;
            let mut at = b.on;
            at[b.axis] = (b.lo + b.hi) / 2.;
            if removed(v.cut.as_ref(), at, direction, b.hi - b.lo, b.diameter) {
                continue;
            }
            let global = bands.iter().all(|(_, other)| {
                other.axis == b.axis
                    && (0..3).all(|k| k == b.axis || (other.on[k] - b.on[k]).abs() < 1e-3)
            }) && (0..3).filter(|&k| k != b.axis).all(|k| {
                ((hi[k] - lo[k]) - bands.iter().map(|(_, b)| b.diameter).fold(0., f64::max)).abs()
                    < 1e-3
            });
            let length = if global {
                at[b.axis] = (lo[b.axis] + hi[b.axis]) / 2.;
                hi[b.axis] - lo[b.axis]
            } else {
                b.hi - b.lo
            };
            if let Some(w) = axis(
                a,
                v,
                format!("ink:{}:{}", v.id, f.id),
                at,
                direction,
                length,
                b.diameter,
                global,
            ) {
                v.line_work.push(w);
            } else if dot(direction, view.toward).abs() > 1. - 1e-6 {
                v.line_work.push(cross(
                    format!("ink:{}:{}", v.id, f.id),
                    page(a, v, at),
                    b.diameter * v.scale / 2.,
                    None,
                ));
            }
        }
        for f in a.features.iter().filter(|f| f.kind == "boss") {
            if let (Some(_at), Some(d), Some(diameter), Some(length)) = (
                holes::point(f.parameters.get("location")),
                holes::point(f.parameters.get("axis")),
                f.parameters.get("diameter").and_then(|x| x.as_f64()),
                f.parameters.get("height").and_then(|x| x.as_f64()),
            ) {
                let d = holes::dir_to_part(&frame, d);
                let at = f.fingerprint.position;
                if removed(v.cut.as_ref(), at, d, length, diameter) {
                    continue;
                }
                if end_on(v, d) {
                    v.line_work.push(end_cross(
                        v,
                        format!("ink:{}:{}", v.id, f.id),
                        page(
                            a,
                            v,
                            holes::to_part(
                                &frame,
                                // Invariant: This branch selected a successfully read hole, whose location is a finite point.
                                holes::point(f.parameters.get("location")).unwrap(),
                            ),
                        ),
                        d,
                        diameter * v.scale / 2.,
                        None,
                    ));
                } else if let Some(w) = axis(
                    a,
                    v,
                    format!("ink:{}:{}", v.id, f.id),
                    at,
                    d,
                    length,
                    diameter,
                    false,
                ) {
                    v.line_work.push(w);
                }
            }
        }
        for (f, s) in crate::lint::channels::seats(a) {
            let Ok(s) = s else {
                continue;
            };
            let mut d = [0.; 3];
            d[s.run_axis] = 1.;
            if removed(v.cut.as_ref(), s.centre, d, s.length, 2. * s.radius) {
                continue;
            }
            if let Some(w) = axis(
                a,
                v,
                format!("ink:{}:{}", v.id, f.id),
                s.centre,
                d,
                s.length,
                2. * s.radius,
                false,
            ) {
                v.line_work.push(w);
            } else if dot(d, view.toward).abs() > 1. - 1e-6 {
                v.line_work.push(cross(
                    format!("ink:{}:{}", v.id, f.id),
                    page(a, v, s.centre),
                    s.radius * v.scale,
                    None,
                ));
            }
        }
        // A grid seen as an even lattice is located by its centre (`dims::holes`), so that
        // origin is marked even where no hole or boss stands on it (#223). Last, so a
        // feature's own cross there keeps its ID as they merge; [`trim`] drops the mark when
        // no dimension locates the grid from it (a hole table instead, say).
        for p in a
            .recognition
            .iter()
            .flat_map(|r| &r.patterns)
            .filter(|p| p.arrangement == "grid")
        {
            if holes::established_with(p, &holes.iter().collect::<Vec<_>>(), &round).is_err() {
                continue;
            }
            let members: Vec<_> = holes
                .iter()
                .filter(|h| p.members.contains(&h.feature.id))
                .collect();
            if members
                .iter()
                .any(|h| !end_on(v, h.direction) || hole_removed(h))
            {
                continue;
            }
            let k = (0..3)
                .max_by(|&i, &j| {
                    let d = members[0].direction;
                    d[i].abs().total_cmp(&d[j].abs())
                })
                // Invariant: The iterator is over the fixed three coordinate axes.
                .expect("three components");
            let centres: Vec<_> = members.iter().map(|h| h.at).collect();
            let Some((_, _, c)) = crate::dims::grid_lattice(&centres, k) else {
                continue;
            };
            v.line_work.push(cross(
                format!("ink:{}:{}", v.id, p.id),
                page(a, v, c),
                hole_diameter(members[0]) * v.scale / 2.,
                None,
            ));
        }
        if v.line_style.centre_marks == Some(false)
            || v.line_style.centre_lines == Some(false)
            || v.id == "v:iso"
        {
            v.line_work.retain(|w| match w.kind.as_str() {
                "cross" => v.line_style.centre_marks.unwrap_or(v.id != "v:iso"),
                "axis" | "pitch_circle" => v.line_style.centre_lines.unwrap_or(v.id != "v:iso"),
                _ => true,
            });
        }
        merge(&mut v.line_work);
        if let Some(b) = &v.broken {
            let centre = crate::detail::crop_extent(a, v).1;
            let mut work = std::mem::take(&mut v.line_work);
            for w in &mut work {
                w.curves = b
                    .items(
                        std::mem::take(&mut w.curves)
                            .into_iter()
                            .map(crate::output::Item::Edge)
                            .collect(),
                        centre,
                        v,
                    )
                    .into_iter()
                    .filter_map(|i| {
                        if let crate::output::Item::Edge(c) = i {
                            Some(c)
                        } else {
                            None
                        }
                    })
                    .collect();
                if let Some(p) = &mut w.centre {
                    *p = b.page(*p, v.at, v.scale, false);
                }
            }
            work.retain(|w| !w.curves.is_empty());
            v.line_work = work;
        }
        if v.detail.is_some() {
            let (size, _) = crate::detail::crop_extent(a, v);
            let rect = [
                v.at[0] - size[0] * v.scale / 2.0,
                v.at[1] - size[1] * v.scale / 2.0,
                v.at[0] + size[0] * v.scale / 2.0,
                v.at[1] + size[1] * v.scale / 2.0,
            ];
            for work in &mut v.line_work {
                let curves = std::mem::take(&mut work.curves);
                work.curves = curves
                    .iter()
                    .cloned()
                    .flat_map(|c| crate::detail::clip(c, rect))
                    .collect();
                // A crop edge is not a feature's overrun; don't trim it further.
                if work.curves != curves {
                    work.overrun = 0.0;
                }
            }
            v.line_work.retain(|w| !w.curves.is_empty());
        }
    }
    let cuts: Vec<_> = views.iter().filter_map(|v| v.cut.clone()).collect();
    for c in cuts {
        if let Some(v) = views.iter_mut().find(|v| v.id == c.marked_in) {
            let at = |p| {
                let q = page(a, v, p);
                v.broken
                    .as_ref()
                    .map_or(q, |b| b.page(q, v.at, v.scale, false))
            };
            let p = at([lo[0], c.origin[1], lo[2]]);
            let q = at([hi[0], c.origin[1], hi[2]]);
            let y = at(c.origin)[1];
            v.line_work.push(LineWork {
                id: format!("ink:{}:cut:{}", v.id, c.label),
                kind: "cutting_plane".into(),
                curves: vec![Curve::Line(
                    [p[0].min(q[0]) - crate::section::OVERRUN, y],
                    [p[0].max(q[0]) + crate::section::OVERRUN, y],
                )],
                centre: None,
                radial: false,
                global_axis: false,
                overrun: 0.,
            });
        }
    }
}

fn fitted_circle(points: &[P], scale: f64) -> Option<(P, f64)> {
    let n = points.len() as f64;
    let mean = [0, 1].map(|k| points.iter().map(|p| p[k]).sum::<f64>() / n);
    let (mut xx, mut xy, mut yy, mut xz, mut yz) = (0., 0., 0., 0., 0.);
    for p in points {
        let (x, y) = (p[0] - mean[0], p[1] - mean[1]);
        let z = x * x + y * y;
        xx += x * x;
        xy += x * y;
        yy += y * y;
        xz += x * z;
        yz += y * z;
    }
    let det = xx * yy - xy * xy;
    if det <= 1e-10 * (xx + yy).powi(2) {
        return None;
    }
    let c = [
        mean[0] + (xz * yy - yz * xy) / (2. * det),
        mean[1] + (yz * xx - xz * xy) / (2. * det),
    ];
    let radii: Vec<_> = points
        .iter()
        .map(|p| (p[0] - c[0]).hypot(p[1] - c[1]))
        .collect();
    let r = radii.iter().sum::<f64>() / n;
    if r <= 0.
        || radii
            .iter()
            .any(|q| (q - r).abs() > (0.01 * scale).max(r * 1e-4))
    {
        return None;
    }
    Some((c, r))
}

fn merge(work: &mut Vec<LineWork>) {
    let mut out: Vec<LineWork> = Vec::new();
    for w in work.drain(..) {
        if w.kind == "cross"
            && let Some(p) = out.iter_mut().find(|p| {
                p.kind == w.kind
                    && p.centre
                        .zip(w.centre)
                        .is_some_and(|(p, q)| (p[0] - q[0]).hypot(p[1] - q[1]) < 1e-6)
            })
        {
            let length = |w: &LineWork| {
                let c = &w.curves[0];
                let (a, b) = (c.start(), c.end());
                (b[0] - a[0]).hypot(b[1] - a[1])
            };
            let size = length(p).max(length(&w));
            // Invariant: The find predicate required p.centre.zip(w.centre) to be Some.
            let c = p.centre.unwrap();
            let basis = if w.radial { &w } else { &*p };
            let curve = &basis.curves[0];
            let (a, b) = (curve.start(), curve.end());
            let n = (b[0] - a[0]).hypot(b[1] - a[1]);
            let u = [(b[0] - a[0]) / n, (b[1] - a[1]) / n];
            p.curves = [u, [-u[1], u[0]]]
                .into_iter()
                .map(|d| {
                    Curve::Line(
                        [c[0] - size * d[0] / 2., c[1] - size * d[1] / 2.],
                        [c[0] + size * d[0] / 2., c[1] + size * d[1] / 2.],
                    )
                })
                .collect();
            p.radial |= w.radial;
            continue;
        }
        if w.kind == "axis" {
            let Curve::Line(a, b) = w.curves[0] else {
                // Invariant: Axis line work is constructed as a single Curve::Line.
                unreachable!()
            };
            let vertical = (a[0] - b[0]).abs() < 1e-6;
            if let Some(p) = out.iter_mut().find(|p| {
                p.kind == "axis" && {
                    let Curve::Line(c, d) = p.curves[0] else {
                        return false;
                    };
                    if vertical {
                        (c[0] - d[0]).abs() < 1e-6
                            && (a[0] - c[0]).abs() < 1e-6
                            && a[1].min(b[1]) <= c[1].max(d[1])
                            && c[1].min(d[1]) <= a[1].max(b[1])
                    } else {
                        (a[1] - b[1]).abs() < 1e-6
                            && (c[1] - d[1]).abs() < 1e-6
                            && (a[1] - c[1]).abs() < 1e-6
                            && a[0].min(b[0]) <= c[0].max(d[0])
                            && c[0].min(d[0]) <= a[0].max(b[0])
                    }
                }
            }) {
                let Curve::Line(c, d) = p.curves[0] else {
                    // Invariant: The find predicate just matched this same axis curve as a Line.
                    unreachable!()
                };
                p.curves[0] = if vertical {
                    Curve::Line(
                        [a[0], a[1].min(b[1]).min(c[1]).min(d[1])],
                        [a[0], a[1].max(b[1]).max(c[1]).max(d[1])],
                    )
                } else {
                    Curve::Line(
                        [a[0].min(b[0]).min(c[0]).min(d[0]), a[1]],
                        [a[0].max(b[0]).max(c[0]).max(d[0]), a[1]],
                    )
                };
                p.global_axis |= w.global_axis;
                // Either end may be the other axis's: only the smaller overrun is sure.
                p.overrun = p.overrun.min(w.overrun);
                continue;
            }
        }
        out.push(w);
    }
    *work = out;
}

/// Moves a view's furniture with the view, when a later layout step shifts the view on the
/// page after its furniture was planned.
pub(crate) fn shift(view: &mut PlannedView, d: P) {
    let m = |q: &mut P| {
        q[0] += d[0];
        q[1] += d[1];
    };
    for w in &mut view.line_work {
        if let Some(c) = &mut w.centre {
            m(c);
        }
        for c in &mut w.curves {
            match c {
                Curve::Line(a, b) => {
                    m(a);
                    m(b);
                }
                Curve::Conic { centre, .. } => m(centre),
                Curve::Beziers(pieces) => pieces.iter_mut().flatten().for_each(m),
                Curve::Rational(pieces) => {
                    for q in pieces.iter_mut().flatten() {
                        q[0] += d[0] * q[2];
                        q[1] += d[1] * q[2];
                    }
                }
            }
        }
    }
}

pub fn segments(w: &LineWork) -> Vec<[P; 2]> {
    w.curves
        .iter()
        .flat_map(|c| {
            let n = match c {
                Curve::Line(..) => 1,
                Curve::Conic { radii, .. } => ((std::f64::consts::PI * radii[0].max(radii[1])
                    / 0.01)
                    .sqrt()
                    .ceil() as usize)
                    .max(32),
                _ => 128,
            };
            (0..n)
                .map(|i| [c.at(i as f64 / n as f64), c.at((i + 1) as f64 / n as f64)])
                .collect::<Vec<_>>()
        })
        .collect()
}

pub(crate) fn obstacles(views: &[PlannedView]) -> Vec<PlannedDimension> {
    let mut out = Vec::new();
    for v in views {
        for w in &v.line_work {
            let mut obstacle = PlannedDimension {
                callout_offset: None,
                reference: false,
                determined_by: Vec::new(),
                region: None,
                id: w.id.clone(),
                kind: "ink".into(),
                view: v.id.clone(),
                value: 0.,
                label: String::new(),
                terms: Vec::new(),
                measurements: Vec::new(),
                default: true,
                from_spec: Vec::new(),
                basis: if w.global_axis {
                    "global_axis".into()
                } else if w.kind == "cross" {
                    "cross".into()
                } else {
                    String::new()
                },
                state: crate::items::State::Shown,
                placed: Some(Placed {
                    lines: segments(w),
                    arrows: Vec::new(),
                    label_at: [0.; 2],
                    rotation: 0.,
                    label_box: [0.; 4],
                }),
            };
            let marks = section_marks(views, v, w);
            if !marks.is_empty()
                && let Curve::Line(a, b) = w.curves[0]
            {
                // Use the renderer's own wing and filled arrow outlines.
                let ends = crate::section::cutting_plane_ends(a[0], b[0], a[1]);
                for item in ends.iter().flatten() {
                    if let crate::output::Item::Face(loops) = item {
                        obstacle
                            .placed
                            .as_mut()
                            // Invariant: The obstacle constructor supplies a placement.
                            .unwrap()
                            .lines
                            .extend(loops.iter().flatten().map(|c| [c.start(), c.end()]));
                    }
                }
                for (id, label_at, label_box) in marks {
                    let mut mark = obstacle.clone();
                    mark.id = id;
                    mark.placed = Some(Placed {
                        lines: Vec::new(),
                        arrows: Vec::new(),
                        label_at,
                        rotation: 0.,
                        label_box,
                    });
                    out.push(mark);
                }
            }
            out.push(obstacle);
        }
    }
    out.extend(crate::detail::caption_obstacles(views));
    out
}

/// The boxes of a cutting plane's printed marks on *v*, by id, with each mark's origin: each
/// end's arrowhead and wing (`<line id>:arrow:<k>`), then its letters (`<line id>:letter:<k>`),
/// as the renderer draws them. Empty for other line work, or a line whose section names no letter.
pub(crate) fn section_marks(
    views: &[PlannedView],
    v: &PlannedView,
    w: &LineWork,
) -> Vec<(String, P, [f64; 4])> {
    let Some(&Curve::Line(a, b)) = w.curves.first().filter(|_| w.kind == "cutting_plane") else {
        return Vec::new();
    };
    let Some(label) = views
        .iter()
        .filter_map(|v| v.cut.as_ref())
        .find(|c| c.marked_in == v.id && w.id == format!("ink:{}:cut:{}", v.id, c.label))
        .map(|c| c.label.as_str())
        // A remote section's carrier is absent, but its planned line keeps the label.
        .or_else(|| w.id.strip_prefix(&format!("ink:{}:cut:", v.id)))
    else {
        return Vec::new();
    };
    let (_, letters) = crate::section::cutting_plane(a[0], b[0], a[1]);
    let ends = crate::section::cutting_plane_ends(a[0], b[0], a[1]);
    let mut out: Vec<_> = ends
        .iter()
        .enumerate()
        // Invariant: Each cutting-plane end holds its finite wing and filled arrow.
        .map(|(k, end)| {
            (
                format!("{}:arrow:{k}", w.id),
                [0.; 2],
                crate::output::items_bbox(end).unwrap(),
            )
        })
        .collect();
    let laid = crate::dims::font().text(label, crate::section::FONT_SIZE);
    out.extend(letters.into_iter().enumerate().map(|(k, at)| {
        (
            format!("{}:letter:{k}", w.id),
            at,
            [
                at[0] + laid.min[0],
                at[1] + laid.min[1],
                at[0] + laid.max[0],
                at[1] + laid.max[1],
            ],
        )
    }));
    out
}

/// The least an axis runs past its feature once [`trim`] has shortened it, and the gap it then
/// keeps from the label, mm (the floor of [`overrun`]).
const MIN_OVERRUN: f64 = 1.0;
const LABEL_GAP: f64 = 0.5;

/// Where a placed label must stay across the end of an axis, shorten that end's overrun past
/// the feature (ISO 128-24) to stop [`LABEL_GAP`] short of the label, keeping at least
/// [`MIN_OVERRUN`]. A label across the feature's own span is left in place and reported by lint.
pub(crate) fn trim(views: &mut [PlannedView], dimensions: &[PlannedDimension]) {
    let boxes: Vec<[f64; 4]> = dimensions
        .iter()
        .filter(|d| d.kind != "ink")
        .filter_map(|d| d.placed.as_ref())
        .map(|p| p.label_box)
        .filter(|b| b[2] > b[0] && b[3] > b[1])
        .map(|b| {
            [
                b[0] - LABEL_GAP,
                b[1] - LABEL_GAP,
                b[2] + LABEL_GAP,
                b[3] + LABEL_GAP,
            ]
        })
        .collect();
    for w in views.iter_mut().flat_map(|v| &mut v.line_work) {
        trim_axis(w, &boxes);
    }
    unused_origins(views, dimensions);
}

/// Drops a hole pattern's own centre mark ([`plan`]) from a view where no dimension locates
/// the pattern: then its centre is no origin, and the mark would read as a missing feature.
pub(crate) fn unused_origins(views: &mut [PlannedView], dimensions: &[PlannedDimension]) {
    for v in views.iter_mut() {
        let prefix = format!("ink:{}:", v.id);
        v.line_work.retain(|w| {
            let Some(p) =
                w.id.strip_prefix(&prefix)
                    .filter(|p| w.kind == "cross" && p.starts_with("f:hole_pattern:"))
            else {
                return true;
            };
            dimensions.iter().any(|d| {
                d.view == v.id
                    && d.measurements
                        .iter()
                        .any(|m| m.feature == p && m.parameter.starts_with("location.length."))
            })
        });
    }
}

/// Once views have their final page positions, an axis overrun that would cross the frame of
/// *page* stops on the frame line, keeping at least [`MIN_OVERRUN`] past its feature. The
/// overrun beyond a feature carries no information; the frame clips nothing else.
pub(crate) fn trim_to_frame(views: &mut [PlannedView], page: [f64; 2]) {
    let (m, far) = (crate::layout::FRAME, 1e6);
    let outside = [
        [-far, -far, m, far],
        [-far, -far, far, m],
        [page[0] - m, -far, far, far],
        [-far, page[1] - m, far, far],
    ];
    for w in views.iter_mut().flat_map(|v| &mut v.line_work) {
        let Some(&Curve::Line(a, b)) = w.curves.first() else {
            continue;
        };
        trim_axis(w, &outside);
        if let Some(&Curve::Line(c, d)) = w.curves.first() {
            let cut = (c[0] - a[0])
                .hypot(c[1] - a[1])
                .max((d[0] - b[0]).hypot(d[1] - b[1]));
            w.overrun = (w.overrun - cut).max(MIN_OVERRUN.min(w.overrun));
        }
    }
}

/// [`trim`] for one piece of line work against the labels' boxes, already grown by the gap.
fn trim_axis(w: &mut LineWork, boxes: &[[f64; 4]]) {
    let spare = w.overrun - MIN_OVERRUN;
    if w.kind != "axis" || spare <= 1e-9 {
        return;
    }
    let Curve::Line(a, b) = w.curves[0] else {
        return;
    };
    let length = (b[0] - a[0]).hypot(b[1] - a[1]);
    let (mut lo, mut hi) = (0.0f64, length);
    for bx in boxes {
        let Some([t0, t1]) = clip([a, b], *bx) else {
            continue;
        };
        let (s0, s1) = (t0 * length, t1 * length);
        if s1 <= spare {
            lo = lo.max(s1);
        } else if s0 >= length - spare {
            hi = hi.min(s0);
        }
    }
    if lo > 0. || hi < length {
        let at = |s: f64| {
            [
                a[0] + (b[0] - a[0]) * s / length,
                a[1] + (b[1] - a[1]) * s / length,
            ]
        };
        w.curves[0] = Curve::Line(at(lo), at(hi));
    }
}

/// The parameter range of segment *s* inside box *b* (Liang-Barsky), if it enters it.
fn clip(s: [P; 2], b: [f64; 4]) -> Option<[f64; 2]> {
    let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (p, q) in [
        (-d[0], s[0][0] - b[0]),
        (d[0], b[2] - s[0][0]),
        (-d[1], s[0][1] - b[1]),
        (d[1], b[3] - s[0][1]),
    ] {
        if p.abs() < 1e-12 {
            if q < 0. {
                return None;
            }
        } else if p < 0. {
            t0 = t0.max(q / p);
        } else {
            t1 = t1.min(q / p);
        }
    }
    (t1 - t0 > 1e-9).then_some([t0, t1])
}

/// Within this distance of a centre stroke, mm, and this close to parallel (sine of the angle),
/// a leader reads as running along the stroke over any shared run longer than [`BESIDE_RUN`]:
/// the leaders' own [`crate::dims`] proximity limits, with ten degrees for near-parallel travel.
const BESIDE: f64 = 1.0;
const BESIDE_SIN: f64 = 0.173_648_177_666_930_3;
const BESIDE_RUN: f64 = 0.5;

/// Whether *line* runs beside the centre stroke *fixed*: near-parallel and within [`BESIDE`]
/// of it for more than [`BESIDE_RUN`] of the leader's length, measured across the leader.
pub(crate) fn runs_beside(line: [P; 2], fixed: [P; 2]) -> bool {
    let u = [line[1][0] - line[0][0], line[1][1] - line[0][1]];
    let v = [fixed[1][0] - fixed[0][0], fixed[1][1] - fixed[0][1]];
    let (lu, lv) = (u[0].hypot(u[1]), v[0].hypot(v[1]));
    if lu < 1e-9 || lv < 1e-9 || (u[0] * v[1] - u[1] * v[0]).abs() > BESIDE_SIN * lu * lv {
        return false;
    }
    let u = [u[0] / lu, u[1] / lu];
    let at = |p: P| (p[0] - line[0][0]) * u[0] + (p[1] - line[0][1]) * u[1];
    let off = |p: P| (p[0] - line[0][0]) * u[1] - (p[1] - line[0][1]) * u[0];
    let (ta, tb, oa, ob) = (at(fixed[0]), at(fixed[1]), off(fixed[0]), off(fixed[1]));
    if (tb - ta).abs() < 1e-9 {
        return false;
    }
    let (mut lo, mut hi) = (ta.min(tb).max(0.), ta.max(tb).min(lu));
    if hi <= lo {
        return false; // travel only beyond a stroke's end is not beside it
    }
    // The stroke's offset is linear in the leader's parameter: keep where it is within BESIDE.
    let slope = (ob - oa) / (tb - ta);
    let offset = |t: f64| oa + slope * (t - ta);
    if slope.abs() < 1e-12 {
        if oa.abs() >= BESIDE {
            return false;
        }
    } else {
        let [x, y] = [-BESIDE, BESIDE].map(|o| ta + (o - oa) / slope);
        lo = lo.max(x.min(y));
        hi = hi.min(x.max(y));
    }
    // A shaft that overlaps a stroke's end can remain within its 1 mm
    // neighbourhood beyond the endpoint. Include that rounded end, as the
    // independent drawing check does; a wholly disjoint projection stays exempt.
    if hi < lo {
        lo = f64::INFINITY;
        hi = f64::NEG_INFINITY;
    }
    for p in fixed {
        let transverse = off(p);
        if transverse.abs() < BESIDE {
            let reach = (BESIDE * BESIDE - transverse * transverse).sqrt();
            let a = (at(p) - reach).max(0.);
            let b = (at(p) + reach).min(lu);
            if b > a {
                lo = lo.min(a);
                hi = hi.max(b);
            }
        }
    }
    hi - lo > BESIDE_RUN && offset((lo + hi) / 2.).abs() < BESIDE
}

/// Only a transverse attachment in the arrow neighbourhood of a marked global
/// turning axis is intentional. Shelf crossings and travel along the axis count.
pub(crate) fn leader_conflict(line: [P; 2], fixed: [P; 2], global: bool, shaft: bool) -> bool {
    let u = [line[1][0] - line[0][0], line[1][1] - line[0][1]];
    let v = [fixed[1][0] - fixed[0][0], fixed[1][1] - fixed[0][1]];
    let w = [fixed[0][0] - line[0][0], fixed[0][1] - line[0][1]];
    let cross = |a: P, b: P| a[0] * b[1] - a[1] * b[0];
    let (lu, lv) = (u[0].hypot(u[1]), v[0].hypot(v[1]));
    if lu < 1e-9 || lv < 1e-9 {
        return false;
    }
    let det = cross(u, v);
    if det.abs() < 1e-9 * lu * lv {
        if cross(w, u).abs() > 1e-6 * lu {
            return false;
        }
        let along = |p: P| ((p[0] - line[0][0]) * u[0] + (p[1] - line[0][1]) * u[1]) / lu;
        let (a, b) = (along(fixed[0]), along(fixed[1]));
        return a.max(b).min(lu) - a.min(b).max(0.) > 1e-6;
    }
    let (t, s) = (cross(w, v) / det, cross(w, u) / det);
    if !(-1e-9..=1. + 1e-9).contains(&t) || !(-1e-9..=1. + 1e-9).contains(&s) {
        return false;
    }
    !(global && shaft && t * lu <= crate::dims::ARROW && det.abs() / (lu * lv) > 0.1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detail_page_mapping_centres_the_crop() {
        let (_, a) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/mounting_plate.step"),
        )
        .unwrap();
        let spec = serde_json::from_value(serde_json::json!({"sheet":{"size":"A2","scale":"1:2"},"views":{"detail_c":{"detail":{"around":"f:hole:1","from":"plan","scale_factor":2}}}})).unwrap();
        let p = crate::model::plan(&a, &spec).unwrap();
        let v = p.views.iter().find(|v| v.id == "v:detail_c").unwrap();
        let bounds = v.detail.as_ref().unwrap().bounds;
        let middle = [0, 1, 2].map(|k| (bounds[0][k] + bounds[1][k]) / 2.);
        assert_eq!(page(&a, v, middle), v.at);
    }

    fn axis_of(a: P, b: P, overrun: f64) -> LineWork {
        LineWork {
            id: "ink:v:side:f:hole:1".into(),
            kind: "axis".into(),
            curves: vec![Curve::Line(a, b)],
            centre: None,
            radial: false,
            global_axis: false,
            overrun,
        }
    }

    fn ends(w: &LineWork) -> [P; 2] {
        [w.curves[0].start(), w.curves[0].end()]
    }

    /// A label across an axis end shortens only that end's overrun, keeping [`MIN_OVERRUN`]
    /// past the feature; across the feature's span, or beyond what the overrun allows, it stays.
    #[test]
    fn labels_trim_only_an_axis_overrun() {
        // The feature spans y 3..68 with 3 mm overrun: the axis runs 0..71.
        let mut w = axis_of([10., 0.], [10., 71.], 3.);
        trim_axis(&mut w, &[[5., 69.8, 20., 72.]]);
        assert_eq!(ends(&w), [[10., 0.], [10., 69.8]]);
        let mut w = axis_of([10., 71.], [10., 0.], 3.);
        trim_axis(&mut w, &[[5., -2., 20., 0.5], [5., 70., 20., 75.]]);
        let [p, q] = ends(&w);
        assert!(
            (p[1] - 70.).abs() < 1e-9 && (q[1] - 0.5).abs() < 1e-9,
            "{p:?} {q:?}"
        );
        for label in [[5., 30., 20., 33.], [5., 67.5, 20., 72.]] {
            let mut w = axis_of([10., 0.], [10., 71.], 3.);
            trim_axis(&mut w, &[label]);
            assert_eq!(ends(&w), [[10., 0.], [10., 71.]], "{label:?}");
        }
        let mut w = axis_of([10., 0.], [10., 71.], 0.);
        trim_axis(&mut w, &[[5., 69.8, 20., 72.]]);
        assert_eq!(ends(&w), [[10., 0.], [10., 71.]]);
    }

    /// Near-parallel travel within a millimetre counts, however slightly the leader tilts;
    /// a crossing at a real angle, or parallel travel further off, does not.
    #[test]
    fn leaders_beside_a_stroke_are_found_at_any_small_angle() {
        let axis = [[121.431, 126.03], [121.431, 197.03]];
        // cgb207's R5: 0.775 mm off and exactly parallel, then tilted onto the axis's end.
        assert!(runs_beside([[122.206, 162.49], [122.206, 126.03]], axis));
        assert!(runs_beside([[122.206, 162.49], [121.456, 126.03]], axis));
        assert!(runs_beside([[121.431, 150.], [121.431, 130.]], axis));
        assert!(!runs_beside([[123.5, 162.49], [123.5, 126.03]], axis));
        assert!(!runs_beside([[115.206, 126.03], [122.206, 162.49]], axis));
        assert!(!runs_beside([[110., 150.], [130., 150.]], axis));
        // #127 adds cgb207's concave rounds. R9.948 formerly overlapped this
        // centre stroke by only 0.013 mm, then ran 1 mm beside its rounded end.
        assert!(runs_beside(
            [[62.66989, 150.89815], [62.66989, 160.74530]],
            [[62.76327, 146.91088], [62.76327, 150.91088]]
        ));
        // Beside only past the stroke's end does not count.
        assert!(!runs_beside([[121.9, 120.], [121.9, 126.]], axis));
    }
    #[test]
    fn oblique_holes_mark_the_projected_ellipse_and_axes() {
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/plate.step");
        let (_, plate) = crate::model::analyse_native(&fixture).unwrap();
        let mut views = crate::model::plan(&plate, &crate::model::Spec::default())
            .unwrap()
            .views;
        let mut a = crate::lint::paired_ramp_steps::tests::part(
            "hole",
            serde_json::json!({"axis":[0.,0.6,0.8], "location":[50.,50.,5.],
                "diameter":20., "depth":10., "through":true}),
        );
        a.features[0].fingerprint.position = [50., 50., 5.];
        assert!(holes::read(&a)[0].1.is_ok());
        plan(&a, &mut views);
        let mut axes = 0;
        let mut marks = 0;
        for v in views.iter().filter(|v| v.id != "v:iso") {
            for w in &v.line_work {
                if w.kind == "axis" {
                    axes += 1;
                } else if w.kind == "cross" {
                    let mut lengths: Vec<_> = w
                        .curves
                        .iter()
                        .map(|c| {
                            let (p, q) = (c.start(), c.end());
                            (q[0] - p[0]).hypot(q[1] - p[1])
                        })
                        .collect();
                    lengths.sort_by(f64::total_cmp);
                    assert!(
                        (lengths[0] - 2. * (8. * v.scale + overrun(8. * v.scale))).abs() < 1e-6
                    );
                    assert!(
                        (lengths[1] - 2. * (10. * v.scale + overrun(10. * v.scale))).abs() < 1e-6
                    );
                    marks += 1;
                }
            }
        }
        assert_eq!(marks, 1);
        assert_eq!(axes, 2);
    }
    #[test]
    fn profiled_bores_have_side_axes_as_well_as_end_crosses() {
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/plate.step");
        let (_, plate) = crate::model::analyse_native(&fixture).unwrap();
        let mut views = crate::model::plan(&plate, &crate::model::Spec::default())
            .unwrap()
            .views;
        let mut a = crate::lint::paired_ramp_steps::tests::part(
            "double_d_bore",
            serde_json::json!({"axis":[0.,0.,1.], "location":[50.,50.,10.],
                "major_diameter":10., "across_flats":7.2, "depth":10., "through":true,
                "flat_direction":[1.,0.,0.]}),
        );
        a.features[0].fingerprint.position = [50., 50., 5.];
        plan(&a, &mut views);
        let mut sides = 0;
        let mut ends = 0;
        for v in views.iter().filter(|v| v.id != "v:iso") {
            let view = View::new(v.direction, v.up).unwrap();
            if dot([0., 0., 1.], view.toward).abs() < 1e-6 {
                let w = v.line_work.iter().find(|w| w.kind == "axis").unwrap();
                let (p, q) = (w.curves[0].start(), w.curves[0].end());
                let length = (q[0] - p[0]).hypot(q[1] - p[1]);
                assert!((length - (10. * v.scale + 2. * overrun(5. * v.scale))).abs() < 1e-6);
                let centre = page(&a, v, a.features[0].fingerprint.position);
                assert!(
                    ((p[0] + q[0]) / 2. - centre[0]).abs() < 1e-6
                        && ((p[1] + q[1]) / 2. - centre[1]).abs() < 1e-6
                );
                sides += 1;
            } else if dot([0., 0., 1.], view.toward).abs() > 1. - 1e-6 {
                assert!(v.line_work.iter().any(|w| w.kind == "cross"));
                ends += 1;
            }
        }
        assert!(sides > 0 && ends > 0);
        views.retain(|v| {
            v.id != "v:iso" && View::new(v.direction, v.up).unwrap().toward[2].abs() > 1. - 1e-6
        });
        a.envelope[1][2] = 50.;
        a.features[0]
            .parameters
            .insert("depth".into(), serde_json::json!(40.));
        a.features[0]
            .parameters
            .insert("location".into(), serde_json::json!([50., 50., 40.]));
        a.features[0].fingerprint.position[2] = 20.;
        views[0].id = "v:section".into();
        views[0].cut = Some(crate::model::Cut {
            label: "test".into(),
            origin: [50., 50., 27.],
            normal: [0., 0., 1.],
            marked_in: "not_shown".into(),
            xref: None,
        });
        plan(&a, &mut views);
        assert!(views[0].line_work.iter().any(|w| w.kind == "cross"));
        views[0].cut.as_mut().unwrap().origin[2] = 45.;
        plan(&a, &mut views);
        assert!(views[0].line_work.is_empty());
    }
    /// A recessed hole's side axis runs from its opening through the counterbore (or
    /// spotface) and the bore, overrunning both ends; with a depth missing there is none.
    #[test]
    fn a_counterbored_hole_axis_spans_recess_and_bore() {
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/plate.step");
        let (_, plate) = crate::model::analyse_native(&fixture).unwrap();
        let mut views = crate::model::plan(&plate, &crate::model::Spec::default())
            .unwrap()
            .views;
        for recess in ["cbore", "spotface"] {
            let mut a = crate::lint::paired_ramp_steps::tests::part(
                "hole",
                serde_json::json!({"axis":[0.,0.,-1.], "location":[50.,50.,10.],
                    "diameter":8., "depth":6., "bottom":"through",
                    recess: {"diameter":16., "depth":4.}}),
            );
            // Deliberately the bore's centroid alone: the axis must not be read from it.
            a.features[0].fingerprint.position = [50., 50., 1.];
            assert!(holes::read(&a)[0].1.is_ok());
            plan(&a, &mut views);
            let mut sides = 0;
            for v in views.iter().filter(|v| v.id != "v:iso") {
                let view = View::new(v.direction, v.up).unwrap();
                if dot([0., 0., 1.], view.toward).abs() > 1e-6 {
                    continue;
                }
                let w = v.line_work.iter().find(|w| w.kind == "axis").unwrap();
                let (p, q) = (w.curves[0].start(), w.curves[0].end());
                let length = (q[0] - p[0]).hypot(q[1] - p[1]);
                assert!(
                    (length - (10. * v.scale + 2. * overrun(4. * v.scale))).abs() < 1e-6,
                    "{recess} {}: {length}",
                    v.id
                );
                let (top, bottom) = (page(&a, v, [50., 50., 10.]), page(&a, v, [50., 50., 0.]));
                let centre = [(top[0] + bottom[0]) / 2., (top[1] + bottom[1]) / 2.];
                assert!(
                    ((p[0] + q[0]) / 2. - centre[0]).abs() < 1e-6
                        && ((p[1] + q[1]) / 2. - centre[1]).abs() < 1e-6
                );
                sides += 1;
            }
            assert!(sides > 0);
            for missing in ["depth", recess] {
                let mut b = a.clone();
                if missing == "depth" {
                    b.features[0].parameters.remove("depth");
                } else {
                    b.features[0]
                        .parameters
                        .insert(recess.into(), serde_json::json!({"diameter":16.}));
                }
                assert!(holes::read(&b)[0].1.is_ok());
                plan(&b, &mut views);
                assert!(
                    views
                        .iter()
                        .all(|v| v.line_work.iter().all(|w| w.kind != "axis")),
                    "{recess} without {missing}"
                );
                assert!(
                    views
                        .iter()
                        .any(|v| v.line_work.iter().any(|w| w.kind == "cross"))
                );
            }
        }
    }
    #[test]
    fn only_a_global_axis_has_a_transverse_tip_exemption() {
        let axis = [[0., -10.], [0., 10.]];
        assert!(!leader_conflict([[-1., 0.], [5., 5.]], axis, true, true));
        assert!(leader_conflict([[-1., 0.], [5., 5.]], axis, false, true));
        assert!(leader_conflict([[-1., 0.], [5., 5.]], axis, true, false));
        assert!(leader_conflict([[0., 0.], [0., 9.]], axis, true, true));
        assert!(leader_conflict([[-5., 0.], [5., 5.]], axis, true, true));
    }
    #[test]
    fn circle_evidence_and_extents_do_not_depend_on_one_part() {
        let points: Vec<_> = (0..8)
            .map(|i| {
                let t = i as f64 * std::f64::consts::TAU / 8.;
                [13. + 20. * t.cos(), -5. + 20. * t.sin()]
            })
            .collect();
        let (c, r) = fitted_circle(&points, 1.).unwrap();
        assert!((c[0] - 13.).abs() < 1e-9 && (c[1] + 5.).abs() < 1e-9 && (r - 20.).abs() < 1e-9);
        let mut wrong = points;
        wrong[0][0] += 1.;
        assert!(fitted_circle(&wrong, 1.).is_none());
        assert!(fitted_circle(&[[0., 0.], [1., 0.], [2., 0.]], 1.).is_none());
        assert_eq!(overrun(0.1), 1.);
        assert_eq!(overrun(10.), 3.);
        assert_eq!(overrun(100.), 5.);

        // A rectangle fits a circle too; a provisional small record needs round corroboration.
        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/plate.step");
        let (_, mut a) = crate::model::analyse_native(&fixture).unwrap();
        let mut views = crate::model::plan(&a, &crate::model::Spec::default())
            .unwrap()
            .views;
        let holes: Vec<_> = holes::read(&a)
            .into_iter()
            .map(|(_, h)| h.unwrap())
            .collect();
        assert_eq!(holes.len(), 4);
        let points: Vec<_> = holes.iter().map(|h| [h.at[0], h.at[1]]).collect();
        let (c, r) = fitted_circle(&points, 1.).unwrap();
        let p = crate::bridge::HolePattern {
            id: "f:hole_pattern:unproven".into(),
            arrangement: "bolt_circle".into(),
            members: holes.iter().map(|h| h.feature.id.clone()).collect(),
            faces: Vec::new(),
            parameters: serde_json::json!({"center":[c[0],c[1],holes[0].at[2]],"diameter":2.*r})
                .as_object()
                .unwrap()
                .clone(),
        };
        a.recognition.as_mut().unwrap().patterns = vec![p];
        plan(&a, &mut views);
        assert!(
            views
                .iter()
                .all(|v| v.line_work.iter().all(|w| w.kind != "pitch_circle"))
        );
    }
}
