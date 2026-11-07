//! Stream K4 (#43 item 2): a callout's leader tip against the physical edge it names, read
//! from the rendered drawing (the tip is the first point of the callout's leader,
//! [`Drawn::lines`]), its view's recorded transform ([`Sheet::frames`]) and the Analysis's
//! geometry of the features the callout claims, never the Plan's sites.
//!
//! - `diameter_leader_target_mismatch` (draftwright's `lint_hole_leader_targets`, ISSUE-1378): a
//!   callout claiming a hole's `bore.diameter` has its tip within [`TOLERANCE`] of a boundary
//!   circle of one of the holes it claims, as projected in its view: the bore's rim at its
//!   opening and, when its depth is known, at its far end; a counterbore's or spotface's rim
//!   at the opening and at its floor; a countersink's at the opening. A tip on another hole's
//!   rim, or in blank material, is a warning. draftwright measures the occurrence's defining
//!   edges; for a recognised round hole those are these circles (its seams lie on them when seen
//!   end-on, where the port calls a hole out). A double-D bore's boundary, seen end-on, is its
//!   trimmed profile: the major circle's two arcs between the flats, and the two flats.
//! - `diameter_leader_target_unverifiable`: a callout whose view has a recorded transform but
//!   none of whose claimed holes can be read (no axis, location or diameter; a double-D bore
//!   not seen end-on) says so.
//! - `radius_leader_target_mismatch`: a straight blend tip misses its named cylindrical
//!   trimmed profile. Exact face evidence is used where present; older straight-blend
//!   analyses use their rolling axis and quarter/half/three-quarter samples.
//! - `radius_leader_not_radial`: the drawn shaft (automatic or pinned) must meet the
//!   middle 25–75% of that profile, within 15 degrees of radial, from either side.
//! - `radius_leader_target_unverifiable`: a straight blend's target cannot be read.
//! - `step_diameter_leader_target_mismatch` (ISSUE-1374-d): a callout claiming a turned step's
//!   `step.diameter` has its tip on the band's own drawn cylinder: its outline each side of its
//!   axis and its end edges, trimmed by the flats that cut it ([`crate::lint::turned::cuts`]),
//!   or, seen along its axis, the arcs left between the flats. `…_unverifiable` (info) when the
//!   record cannot be read.
//! - `chamfer_leader_target_mismatch` (ISSUE-1374-a): a `chamfer.length` or
//!   `oriented_chamfer.length` tip lies on the bevel: a turned bevel's meridian, turned about its
//!   coaxial band onto the outline (or its ring, seen along the axis); a prismatic bevel's
//!   section seen along its edge; an oriented bevel's face. `…_unverifiable` (info) when the view
//!   cannot show it (a prismatic edge not seen end-on) or the record cannot be read.
//! - `flat_leader_target_mismatch` (ARCH0017-6): a `flat.length` tip lies on the flat's face: the
//!   chord its plane leaves on each turned band it cuts, over the span it cuts, or on stock no
//!   band records its plane within the part's box over its stock span.
//! - `recess_leader_target_mismatch` (ARCH0017-6): a section recess's tip lies on the faces its
//!   record publishes, its section swept along its run: walls, floor and capped ends, never its
//!   opening or an open end (a through pocket's mouth, a seat's chord).
//! - `slot_leader_target_mismatch` (ARCH0017-6): a slot's tip lies on its walls: a through slot's
//!   outline swept through it (a slot pattern's member slots'), an oriented slot's passage
//!   section swept along its run.
//!   For all of these, a callout counting several members is a mismatch only when every member
//!   could be measured; a tip that misses the measurable ones while others cannot be verified is
//!   `…_unverifiable` (info), since it may sit on one of those.
//! - `step_length_witness_target_mismatch` (ISSUE-1374-d): a linear dimension claiming a turned
//!   step's `step.length` has its witness stations at the band's projected shoulders.
//!
//! Credit goes with the ink (ISSUE-1374-a/d): a chamfer or turned-diameter callout whose tip is
//! proven off every member's face (or a chamfer callout in a view that can show none of its
//! bevels), and a turned length whose witnesses miss its band, lose the claims [`Withdrawn`]
//! names, so the ledgers report those sizes missing rather than credit them.
//!
//! - `leader_crosses_silhouette` (draftwright's structural check, #796/#798): a callout's shaft,
//!   after leaving the material at its tip, cuts back into it in its own view, measured by
//!   [`crate::material::reentry`], the predicate the leader router prices
//!   (`dims::leaders`), on the material [`crate::material::solids`] builds from the Analysis
//!   and the view's recorded transform. Info, with the depth cut, as in draftwright.
//!
//! A fillet carrying equal blend claims is judged against its own fillet faces.
//! An annotation whose view has no recorded transform (a captured Python sheet) is not judged,
//! as in [`super::fidelity`].

use std::collections::BTreeMap;
use std::f64::consts::PI;

use super::{LintIssue, Severity};
use crate::bridge::Feature;
use crate::bridge::holes::{self, Hole, dir_to_part, point, to_part};
use crate::model::Analysis;
use crate::output::{Drawing, DrawnFrame, Kind, P};

/// How far a tip may lie from the edge it names, page mm (draftwright's `2e-3`).
pub const TOLERANCE: f64 = 2e-3;

type V3 = [f64; 3];

fn dot(a: V3, b: V3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn unit(a: V3) -> Option<V3> {
    let n = dot(a, a).sqrt();
    (n > 1e-12 && n.is_finite()).then(|| a.map(|x| x / n))
}

/// A view as drawn: its projection, and where the part's centre lands, as
/// [`super::coverage::drawn_centre_marks`] projects.
struct Projection {
    broken: Option<crate::output::BreakMap>,
    view: haecceity::hlr::View,
    at: P,
    centre: [f64; 2],
    scale: f64,
}

impl Projection {
    fn new(analysis: &Analysis, f: &DrawnFrame) -> Option<Projection> {
        let view = haecceity::hlr::View::new(f.direction, f.up)?;
        let [lo, hi] = analysis.envelope;
        let centre = view.map([0, 1, 2].map(|k| 0.5 * (lo[k] + hi[k])));
        (f.scale.is_finite() && f.scale > 0.0).then_some(Projection {
            view,
            broken: f.broken.clone(),
            at: f.at,
            centre,
            scale: f.scale,
        })
    }

    fn page(&self, p: V3) -> P {
        let q = self.view.map(p);
        let q = self.broken.as_ref().map_or(q, |b| b.map(q));
        [0, 1].map(|k| self.at[k] + self.scale * (q[k] - self.centre[k]))
    }

    /// Whether direction *n* is along the line of sight.
    fn end_on(&self, n: V3) -> bool {
        unit(n).is_some_and(|n| dot(n, self.view.toward).abs() > 1.0 - 1e-9)
    }

    /// The page distance from *tip* to the circle of radius *r* about *c* square to *n*.
    fn circle_gap(&self, c: V3, n: V3, r: f64, tip: P) -> f64 {
        let Some(n) = unit(n) else {
            return f64::INFINITY;
        };
        if self.end_on(n) {
            let p = self.page(c);
            return ((tip[0] - p[0]).hypot(tip[1] - p[1]) - self.scale * r).abs();
        }
        // An oblique or side-on circle projects to an ellipse or a segment: the nearest of
        // 720 points, refined by golden section between its neighbours.
        let a = if n[0].abs() < 0.9 {
            [1.0, 0.0, 0.0]
        } else {
            [0.0, 1.0, 0.0]
        };
        let e1 = unit([
            a[1] * n[2] - a[2] * n[1],
            a[2] * n[0] - a[0] * n[2],
            a[0] * n[1] - a[1] * n[0],
        ])
        // Invariant: The circle normal is normalized and the chosen seed is not parallel to it.
        .unwrap();
        let e2 = [
            n[1] * e1[2] - n[2] * e1[1],
            n[2] * e1[0] - n[0] * e1[2],
            n[0] * e1[1] - n[1] * e1[0],
        ];
        let gap = |t: f64| {
            let (s, co) = t.sin_cos();
            let p = self.page([0, 1, 2].map(|k| c[k] + r * (co * e1[k] + s * e2[k])));
            (tip[0] - p[0]).hypot(tip[1] - p[1])
        };
        const N: usize = 720;
        let step = 2.0 * PI / N as f64;
        let k = (0..N)
            .min_by(|&i, &j| gap(i as f64 * step).total_cmp(&gap(j as f64 * step)))
            .unwrap_or(0);
        let (mut lo, mut hi) = ((k as f64 - 1.0) * step, (k as f64 + 1.0) * step);
        let g = 0.5 * (5f64.sqrt() - 1.0);
        for _ in 0..60 {
            let (x, y) = (hi - g * (hi - lo), lo + g * (hi - lo));
            if gap(x) < gap(y) {
                hi = y;
            } else {
                lo = x;
            }
        }
        gap(0.5 * (lo + hi)).min(gap(k as f64 * step))
    }
}

/// The boundary circles of hole *h*: centre, axis and radius.
fn rims(h: &Hole) -> Vec<(V3, V3, f64)> {
    let at = |d: f64| [0, 1, 2].map(|k| h.at[k] + d * h.direction[k]);
    let mut out = vec![(h.at, h.direction, 0.5 * h.diameter)];
    if let Some(d) = h.depth {
        out.push((at(d), h.direction, 0.5 * h.diameter));
    }
    // The recesses from the opening inward, each from where the one outside it ends: each
    // depth is its own mouth-to-floor span.
    let recesses: Vec<_> = h.recesses().collect();
    let mut mouth = Some(0.0);
    for r in recesses.iter().rev() {
        out.push((h.at, h.direction, 0.5 * r.diameter));
        if let Some(m) = mouth {
            out.push((at(m), h.direction, 0.5 * r.diameter));
        }
        mouth = mouth.zip(r.depth).map(|(m, d)| m + d);
        if let Some(d) = mouth {
            out.push((at(d), h.direction, 0.5 * r.diameter));
            out.push((at(d), h.direction, 0.5 * h.diameter));
        }
    }
    if let Some(c) = h.countersink {
        out.push((h.at, h.direction, 0.5 * c.diameter));
    }
    out.retain(|(c, _, r)| r.is_finite() && *r > 0.0 && c.iter().all(|x| x.is_finite()));
    out
}

/// The page distance from *tip* to double-D bore *f*'s trimmed profile, seen end-on in *p*: the
/// major circle's arcs between the flats, and the two flats. None when the bore is not seen
/// end-on; an error when its record cannot be read.
fn double_d_gap(
    analysis: &Analysis,
    p: &Projection,
    f: &Feature,
    tip: P,
) -> Result<Option<f64>, String> {
    let frame = holes::frame(analysis);
    let q = &f.parameters;
    let axis = point(q.get("axis")).ok_or("its record gives no axis")?;
    let at = point(q.get("location")).ok_or("its record gives no location")?;
    let flats = point(q.get("flat_direction")).ok_or("its record gives no flat direction")?;
    let size = |name: &str| {
        q.get(name)
            .and_then(serde_json::Value::as_f64)
            .filter(|x| x.is_finite() && *x > 0.0)
            .ok_or(format!("its record gives no {name}"))
    };
    let (major, across) = (size("major_diameter")?, size("across_flats")?);
    if !p.end_on(dir_to_part(&frame, axis)) {
        return Ok(None);
    }
    let at = to_part(&frame, at);
    let c = p.page(at);
    let n = unit(dir_to_part(&frame, flats)).ok_or("its flat direction is a zero vector")?;
    let e = p.page([0, 1, 2].map(|k| at[k] + n[k]));
    let (nx, ny) = ((e[0] - c[0]) / p.scale, (e[1] - c[1]) / p.scale);
    let (r, a) = (0.5 * p.scale * major, 0.5 * p.scale * across.min(major));
    let w = (r * r - a * a).max(0.0).sqrt();
    let v = [tip[0] - c[0], tip[1] - c[1]];
    let (along, across) = (v[0] * nx + v[1] * ny, v[1] * nx - v[0] * ny);
    let d = along.hypot(across);
    // The arcs: the nearest point of the circle, when it lies between the flats.
    let mut gap = if d > 1e-12 && (r * along / d).abs() <= a {
        (d - r).abs()
    } else {
        f64::INFINITY
    };
    // The flats, each from end to end (their ends are the arcs' ends).
    for side in [-a, a] {
        let t = across.clamp(-w, w);
        gap = gap.min((along - side).hypot(across - t));
    }
    Ok(Some(gap))
}

/// The page distance from *t* to segment *a*–*b*.
fn segment_gap(a: P, b: P, t: P) -> f64 {
    let d = [b[0] - a[0], b[1] - a[1]];
    let n = d[0] * d[0] + d[1] * d[1];
    let s = if n < 1e-24 {
        0.0
    } else {
        (((t[0] - a[0]) * d[0] + (t[1] - a[1]) * d[1]) / n).clamp(0.0, 1.0)
    };
    (t[0] - a[0] - s * d[0]).hypot(t[1] - a[1] - s * d[1])
}

/// The page distance from *t* to the convex quadrilateral *q* (zero inside it).
fn quad_gap(q: [P; 4], t: P) -> f64 {
    let side = |i: usize| {
        let (a, b) = (q[i], q[(i + 1) % 4]);
        (b[0] - a[0]) * (t[1] - a[1]) - (b[1] - a[1]) * (t[0] - a[0])
    };
    let sides = [0, 1, 2, 3].map(side);
    if sides.iter().all(|s| *s >= 0.0) || sides.iter().all(|s| *s <= 0.0) {
        return 0.0;
    }
    (0..4)
        .map(|i| segment_gap(q[i], q[(i + 1) % 4], t))
        .fold(f64::INFINITY, f64::min)
}

/// The page distance from *tip* to turned step *f*'s cylinder as *p* draws it: its outline on each
/// side of its axis, and its end edges, trimmed by the flats that cut it; or, seen along its axis,
/// its trimmed circle. Err when its record cannot be read.
fn step_gap(analysis: &Analysis, p: &Projection, f: &Feature, tip: P) -> Result<f64, String> {
    use super::turned as t;
    let b = t::band(&holes::frame(analysis), f)?;
    let cuts = t::cuts(analysis, &b);
    let mut a = [0.0; 3];
    a[b.axis] = 1.0;
    let mut stations = vec![b.lo, b.hi];
    stations.extend(cuts.iter().flat_map(|c| [c.lo, c.hi]));
    stations.sort_by(f64::total_cmp);
    if p.end_on(a) {
        let mut gap = f64::INFINITY;
        for w in stations.windows(2).filter(|w| w[1] - w[0] > 1e-9) {
            for arc in t::arcs(&b, &cuts, 0.5 * (w[0] + w[1])) {
                let (c, r, span) = arc.projected(|q| p.page(q));
                let d = (tip[0] - c[0]).hypot(tip[1] - c[1]);
                if span.contains(
                    (tip[1] - c[1]).atan2(tip[0] - c[0]),
                    TOLERANCE / r.max(1e-9),
                ) {
                    gap = gap.min((d - r).abs());
                }
            }
        }
        return Ok(gap);
    }
    let t3 = p.view.toward;
    let u = unit([
        t3[1] * a[2] - t3[2] * a[1],
        t3[2] * a[0] - t3[0] * a[2],
        t3[0] * a[1] - t3[1] * a[0],
    ])
    .ok_or("the view looks along its axis")?;
    let mut gap = f64::INFINITY;
    for d in [u, u.map(|x| -x)] {
        for [q0, q1] in t::outline(&b, &cuts, d) {
            gap = gap.min(segment_gap(p.page(q0), p.page(q1), tip));
        }
    }
    // The end edges, from one side's outline to the other's.
    for end in [b.lo, b.hi] {
        let inside = if end == b.lo {
            b.lo + 1e-6
        } else {
            b.hi - 1e-6
        };
        if let (Some(mut q0), Some(mut q1)) = (
            t::extreme(&b, &cuts, inside, u),
            t::extreme(&b, &cuts, inside, u.map(|x| -x)),
        ) {
            q0[b.axis] = end;
            q1[b.axis] = end;
            gap = gap.min(segment_gap(p.page(q0), p.page(q1), tip));
        }
    }
    Ok(gap)
}

/// The page distance from *tip* to chamfer *f*'s bevel as *p* draws it, or why the view cannot
/// show it. A turned bevel (a cone about its coaxial band) reads in profile as its meridian
/// through its record's point turned onto the outline, either way the bevel may slope; or, seen
/// along its axis, as the ring it covers. A prismatic bevel reads seen along its edge, as its
/// section through the record's point, its legs along the two axes square to the edge. An oriented
/// bevel is its face: the quadrilateral its legs and run span.
fn chamfer_gap(analysis: &Analysis, p: &Projection, f: &Feature, tip: P) -> Result<f64, String> {
    let frame = holes::frame(analysis);
    if f.kind == "oriented_chamfer" {
        let b = super::oriented_chamfers::read(analysis, f)?;
        let along = |q: V3| dot([0, 1, 2].map(|k| q[k] - b.corner[k]), b.run);
        let mid = along(b.at);
        let at = |leg: V3, l: f64, s: f64| {
            p.page(
                [0, 1, 2].map(|k| b.corner[k] + l * leg[k] + (mid + s * 0.5 * b.length) * b.run[k]),
            )
        };
        return Ok(quad_gap(
            [
                at(b.u, b.legs[0], -1.0),
                at(b.u, b.legs[0], 1.0),
                at(b.v, b.legs[1], 1.0),
                at(b.v, b.legs[1], -1.0),
            ],
            tip,
        ));
    }
    let q = &f.parameters;
    let at = point(q.get("at"))
        .map(|l| to_part(&frame, l))
        .ok_or("its record gives no point on it (at)")?;
    let k = q
        .get("axis")
        .and_then(serde_json::Value::as_str)
        .and_then(|a| ["x", "y", "z"].iter().position(|x| *x == a))
        .and_then(|i| {
            let mut d = [0.0; 3];
            d[i] = 1.0;
            holes::principal(dir_to_part(&frame, d))
        })
        .ok_or("its record gives no principal axis")?;
    let leg = |name: &str| {
        q.get(name)
            .and_then(serde_json::Value::as_f64)
            .filter(|x| x.is_finite() && *x > 0.0)
            .ok_or(format!("its record gives no {name}"))
    };
    let (l1, l2) = (leg("leg1")?, leg("leg2")?);
    let mut a = [0.0; 3];
    a[k] = 1.0;
    // The bevel's section through *centre*, along *along* and *across* (unit, square to each
    // other), either way it slopes and either way round its legs lie.
    let section = |centre: V3, along: V3, across: V3| {
        let mut gap = f64::INFINITY;
        for (la, lr) in [(l1, l2), (l2, l1)] {
            for s in [-1.0, 1.0] {
                let e = [0, 1, 2].map(|i| 0.5 * (la * along[i] + s * lr * across[i]));
                let q0 = p.page([0, 1, 2].map(|i| centre[i] + e[i]));
                let q1 = p.page([0, 1, 2].map(|i| centre[i] - e[i]));
                gap = gap.min(segment_gap(q0, q1, tip));
            }
        }
        gap
    };
    if q.get("turned").and_then(serde_json::Value::as_bool) == Some(true) {
        let bands = super::turned::all_bands(analysis);
        // Its coaxial band's axis, or the one axis line every band about *k* shares (a part
        // turned whole, as the planner turns it).
        let (j, m) = ((k + 1) % 3, (k + 2) % 3);
        let along: Vec<&super::turned::Band> = bands.iter().filter(|b| b.axis == k).collect();
        let on = super::turned::coaxial(&bands, k, at, l1.max(l2))
            .map(|b| b.on)
            .or_else(|| {
                let first = along.first()?;
                along
                    .iter()
                    .all(|b| {
                        (b.on[j] - first.on[j]).abs() <= 1e-3
                            && (b.on[m] - first.on[m]).abs() <= 1e-3
                    })
                    .then_some(first.on)
            })
            .ok_or("no coaxial turned band holds its station")?;
        let b = super::turned::Band {
            axis: k,
            on,
            lo: at[k],
            hi: at[k],
            diameter: 0.0,
        };
        let rho = (at[j] - b.on[j]).hypot(at[m] - b.on[m]);
        if p.end_on(a) {
            let c = p.page(b.on);
            let d = (tip[0] - c[0]).hypot(tip[1] - c[1]);
            return Ok(((d - p.scale * rho).abs() - p.scale * 0.5 * l1.max(l2)).max(0.0));
        }
        let t3 = p.view.toward;
        let u = unit([
            t3[1] * a[2] - t3[2] * a[1],
            t3[2] * a[0] - t3[0] * a[2],
            t3[0] * a[1] - t3[1] * a[0],
        ])
        .ok_or("the view looks along its axis")?;
        let mut gap = f64::INFINITY;
        for w in [u, u.map(|x| -x)] {
            let mut o = b.on;
            o[k] = at[k];
            let o = [0, 1, 2].map(|i| o[i] + rho * w[i]);
            gap = gap.min(section(o, a, w));
        }
        return Ok(gap);
    }
    if !p.end_on(a) {
        return Err("the view does not see its edge end-on, where its bevel shows edge-on".into());
    }
    let (j, m) = ((k + 1) % 3, (k + 2) % 3);
    let (mut ej, mut em) = ([0.0; 3], [0.0; 3]);
    ej[j] = 1.0;
    em[m] = 1.0;
    Ok(section(at, ej, em))
}

/// The page distance from *t* to polygon *ring* (zero inside it; a polygon of no area, such as a
/// face seen edge-on, is its outline).
fn polygon_gap(ring: &[P], t: P) -> f64 {
    let n = ring.len();
    if n == 0 {
        return f64::INFINITY;
    }
    let edge = (0..n)
        .map(|i| segment_gap(ring[i], ring[(i + 1) % n], t))
        .fold(f64::INFINITY, f64::min);
    let area: f64 = (0..n)
        .map(|i| {
            let (a, b) = (ring[i], ring[(i + 1) % n]);
            a[0] * b[1] - b[0] * a[1]
        })
        .sum();
    if area.abs() < 1e-9 {
        return edge;
    }
    let mut inside = false;
    for i in 0..n {
        let (a, b) = (ring[i], ring[(i + 1) % n]);
        if (a[1] > t[1]) != (b[1] > t[1])
            && t[0] < a[0] + (t[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        {
            inside = !inside;
        }
    }
    if inside { 0.0 } else { edge }
}

/// The points of the section edge from *a* to *b* with *bulge* (the tangent of a quarter of its
/// arc's sweep, signed as quiddity's profiles sign it: the arc's middle lies at
/// `mid + bulge / 2 · (dy, −dx)`), no chord sagging more than *sag* from the arc. A straight edge
/// is its two ends.
fn edge_points(a: [f64; 2], b: [f64; 2], bulge: f64, sag: f64) -> Vec<[f64; 2]> {
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    let chord = dx.hypot(dy);
    if bulge == 0.0 || !bulge.is_finite() || chord < 1e-12 {
        return vec![a, b];
    }
    let mid = [0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])];
    let f = (1.0 - bulge * bulge) / (4.0 * bulge);
    let c = [mid[0] - f * dy, mid[1] + f * dx];
    let r = chord * (1.0 + bulge * bulge) / (4.0 * bulge.abs());
    let middle = [mid[0] + 0.5 * bulge * dy, mid[1] - 0.5 * bulge * dx];
    let start = (a[1] - c[1]).atan2(a[0] - c[0]);
    let size = 4.0 * bulge.abs().atan();
    let on = |t: f64| [c[0] + r * t.cos(), c[1] + r * t.sin()];
    let off = |q: [f64; 2]| (q[0] - middle[0]).hypot(q[1] - middle[1]);
    let sweep = if off(on(start + 0.5 * size)) <= off(on(start - 0.5 * size)) {
        size
    } else {
        -size
    };
    let step = if sag < r {
        2.0 * (1.0 - sag / r).acos()
    } else {
        size
    };
    let n = ((size / step.max(1e-9)).ceil() as usize).clamp(1, 4096);
    let mut out: Vec<[f64; 2]> = (0..n)
        .map(|i| on(start + sweep * i as f64 / n as f64))
        .collect();
    out[0] = a;
    out.push(b);
    out
}

/// A prism in the part's frame: a section boundary (points and bulges in `u`, `v`) swept along
/// `run` over `interval`, its ends capped square to the run (`Some(true)`), open (`Some(false)`)
/// or capped by another surface (`None`). An open boundary has no wall from its last point back
/// to its first: that gap is its opening.
struct Prism {
    origin: V3,
    run: V3,
    u: V3,
    v: V3,
    interval: [f64; 2],
    boundary: Vec<([f64; 2], f64)>,
    closed: bool,
    caps: [Option<bool>; 2],
}

impl Prism {
    fn at(&self, q: [f64; 2], s: f64) -> V3 {
        [0, 1, 2].map(|k| self.origin[k] + q[0] * self.u[k] + q[1] * self.v[k] + s * self.run[k])
    }

    /// The page distance from *tip* to the prism's faces as *p* draws them: its walls (each
    /// boundary edge swept along the run) and its capped ends, never its opening or open ends.
    /// Err when the tip misses them and an end capped by a surface not square to the run, which
    /// lint cannot place, might hold it.
    fn gap(&self, p: &Projection, tip: P) -> Result<f64, String> {
        let sag = TOLERANCE / (4.0 * p.scale);
        let n = self.boundary.len();
        let edges = if self.closed { n } else { n.saturating_sub(1) };
        let walls: Vec<Vec<[f64; 2]>> = (0..edges)
            .map(|i| {
                let (a, bulge) = self.boundary[i];
                edge_points(a, self.boundary[(i + 1) % n].0, bulge, sag)
            })
            .collect();
        let [lo, hi] = self.interval;
        let mut gap = f64::INFINITY;
        for wall in &walls {
            for w in wall.windows(2) {
                let quad = [
                    p.page(self.at(w[0], lo)),
                    p.page(self.at(w[1], lo)),
                    p.page(self.at(w[1], hi)),
                    p.page(self.at(w[0], hi)),
                ];
                gap = gap.min(polygon_gap(&quad, tip));
            }
        }
        // A capped end is the region the boundary encloses. An open boundary closes across its
        // opening; an open chain of straight supports square to each other (an edge-open
        // pocket's, a rectangular slot's) bounds the rectangle of its extents, its free sides
        // the part's own edges, as the drafting grammar reads it (`section_recesses::pocket`).
        let square = self.boundary.windows(2).all(|w| {
            w[0].1 == 0.0
                && (0..2)
                    .filter(|&i| (w[0].0[i] - w[1].0[i]).abs() > 1e-9)
                    .count()
                    == 1
        });
        // Only a corner or a U (two or three supports) is read so: a longer chain, such as a
        // stepped edge, would floor area outside the pocket, so it closes across its opening.
        let ring: Vec<[f64; 2]> = if !self.closed && square && (3..=4).contains(&n) {
            let (mut lo, mut hi) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
            for (q, _) in &self.boundary {
                for i in 0..2 {
                    lo[i] = lo[i].min(q[i]);
                    hi[i] = hi[i].max(q[i]);
                }
            }
            vec![lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]]
        } else {
            walls
                .iter()
                .flat_map(|w| w[..w.len() - 1].iter().copied())
                .chain((!self.closed).then(|| self.boundary[n - 1].0))
                .collect()
        };
        let mut unplaced = false;
        for (cap, s) in self.caps.iter().zip(self.interval) {
            match cap {
                Some(true) => {
                    let face: Vec<P> = ring.iter().map(|q| p.page(self.at(*q, s))).collect();
                    gap = gap.min(polygon_gap(&face, tip));
                }
                Some(false) => {}
                None => unplaced = true,
            }
        }
        if gap > TOLERANCE && unplaced {
            return Err("an end of it is capped by a surface not square to its run".into());
        }
        Ok(gap)
    }
}

/// The unit axis of the part numbered *k*.
fn axis(k: usize) -> V3 {
    let mut a = [0.0; 3];
    a[k] = 1.0;
    a
}

/// The page distance from *tip* to flat *f*'s face as *p* draws it: on each turned band it cuts
/// ([`super::turned::flat_cuts`]), the chord of the band's circle its plane leaves, over the span
/// it cuts; on stock no band records, its plane within the part's box over its stock span. Err
/// when its record cannot be read.
fn flat_gap(analysis: &Analysis, p: &Projection, f: &Feature, tip: P) -> Result<f64, String> {
    use super::turned as t;
    let mut gap = f64::INFINITY;
    let mut seen = false;
    for b in t::all_bands(analysis) {
        for (_, cut) in t::flat_cuts(analysis, &b)
            .into_iter()
            .filter(|(g, _)| g.id == f.id)
        {
            seen = true;
            let r = 0.5 * b.diameter;
            let h = (r * r - cut.distance * cut.distance).max(0.0).sqrt();
            let a = axis(b.axis);
            let n = cut.normal;
            let w = [
                a[1] * n[2] - a[2] * n[1],
                a[2] * n[0] - a[0] * n[2],
                a[0] * n[1] - a[1] * n[0],
            ];
            let corner = |s: f64, side: f64| {
                let mut c = b.on;
                c[b.axis] = s;
                p.page([0, 1, 2].map(|k| c[k] + cut.distance * n[k] + side * h * w[k]))
            };
            let quad = [
                corner(cut.lo, -1.0),
                corner(cut.lo, 1.0),
                corner(cut.hi, 1.0),
                corner(cut.hi, -1.0),
            ];
            gap = gap.min(polygon_gap(&quad, tip));
        }
    }
    if seen {
        return Ok(gap);
    }
    // On stock no turned band records: its plane within the part's box over its stock span,
    // which a view along its plane draws as the plane's own line.
    let q = holes::frame(analysis);
    let r = &f.parameters;
    let plane = r.get("flat_plane");
    let (Some(at), Some(dir), Some(origin), Some(normal), Some(span)) = (
        point(r.get("at")),
        point(r.get("axis_direction")),
        point(plane.and_then(|p| p.get("at"))),
        point(plane.and_then(|p| p.get("normal"))),
        r.get("stock_span").and_then(serde_json::Value::as_array),
    ) else {
        return Err("its record gives no plane, axis or stock span".into());
    };
    let [Some(s0), Some(s1)] = [0, 1].map(|i| span.get(i).and_then(serde_json::Value::as_f64))
    else {
        return Err("its record gives no stock span".into());
    };
    let k =
        holes::principal(dir_to_part(&q, dir)).ok_or("its axis lies along no axis of the part")?;
    let n = unit(dir_to_part(&q, normal)).ok_or("its plane's normal is a zero vector")?;
    let o = to_part(&q, origin);
    let along = dot(at, dir);
    let [e0, e1] =
        [s0, s1].map(|s| to_part(&q, [0, 1, 2].map(|i| at[i] + (s - along) * dir[i]))[k]);
    let [mut lo, mut hi] = analysis.envelope;
    (lo[k], hi[k]) = (e0.min(e1), e0.max(e1));
    // The plane's section of the box: where it crosses the box's twelve edges, in order about
    // their centre.
    let mut points: Vec<V3> = Vec::new();
    for i in 0..3 {
        let (j, m) = ((i + 1) % 3, (i + 2) % 3);
        for (cj, cm) in [
            (lo[j], lo[m]),
            (lo[j], hi[m]),
            (hi[j], lo[m]),
            (hi[j], hi[m]),
        ] {
            let mut a = [0.0; 3];
            a[j] = cj;
            a[m] = cm;
            a[i] = lo[i];
            let mut b = a;
            b[i] = hi[i];
            let (da, db) = (dot(n, a) - dot(n, o), dot(n, b) - dot(n, o));
            if da * db <= 0.0 && (da - db).abs() > 1e-12 {
                let t = da / (da - db);
                points.push([0, 1, 2].map(|c| a[c] + t * (b[c] - a[c])));
            }
        }
    }
    if points.len() < 2 {
        return Err("its plane does not cross the part's box over its stock span".into());
    }
    let c = [0, 1, 2].map(|i| points.iter().map(|p| p[i]).sum::<f64>() / points.len() as f64);
    let e1 = unit([0, 1, 2].map(|i| points[0][i] - c[i])).unwrap_or(n);
    let e2 = [
        n[1] * e1[2] - n[2] * e1[1],
        n[2] * e1[0] - n[0] * e1[2],
        n[0] * e1[1] - n[1] * e1[0],
    ];
    let angle = |q: &V3| {
        let d = [0, 1, 2].map(|i| q[i] - c[i]);
        dot(d, e2).atan2(dot(d, e1))
    };
    points.sort_by(|a, b| angle(a).total_cmp(&angle(b)));
    let face: Vec<P> = points.iter().map(|q| p.page(*q)).collect();
    Ok(polygon_gap(&face, tip))
}

/// The page distance from *tip* to section recess *f*'s faces as *p* draws them: its published
/// section swept along its run (`super::section_recesses::swept`): walls, floor and capped ends,
/// never its open mouth.
fn recess_gap(analysis: &Analysis, p: &Projection, f: &Feature, tip: P) -> Result<f64, String> {
    let s = super::section_recesses::swept(f).map_err(|r| r.reason().to_string())?;
    let frame = holes::frame(analysis);
    let unit3 = |d: V3, what: &str| {
        unit(dir_to_part(&frame, d)).ok_or(format!("its frame's {what} is a zero vector"))
    };
    Prism {
        origin: to_part(&frame, s.origin),
        run: unit3(s.run, "run")?,
        u: unit3(s.u, "u")?,
        v: unit3(s.v, "v")?,
        interval: s.interval,
        boundary: s.boundary,
        closed: s.closed,
        caps: s.caps,
    }
    .gap(p, tip)
}

/// The page distance from *tip* to slot *f*'s walls as *p* draws them: a through slot's outline
/// (its ends semicircles when its end radius is half its width) swept through it; an oriented
/// slot's outline swept through its depth, along its own axes.
fn slot_gap(analysis: &Analysis, p: &Projection, f: &Feature, tip: P) -> Result<f64, String> {
    if f.kind == "oriented_slot" {
        let s = super::oriented_slots::passage(analysis, f)?;
        return Prism {
            origin: s.origin,
            run: s.run,
            u: s.u,
            v: s.v,
            interval: s.interval,
            boundary: s.boundary,
            closed: s.closed,
            caps: [Some(false); 2],
        }
        .gap(p, tip);
    }
    let s = super::slots::outline(analysis, f)?;
    // quiddity rounds the end radius to 0.01 mm (nist_ftc_09's 6.35 wide slots record R3.18):
    // full-round ends are half the width, as the slot's arcs are drawn.
    let end_radius = s.end_radius.map(|r| {
        if (2.0 * r - s.width).abs() <= super::slots::FULL_ROUND {
            0.5 * s.width
        } else {
            r
        }
    });
    let boundary = super::oriented_slots::outline(s.width, s.ends, s.centre, end_radius);
    Prism {
        origin: [0.0; 3],
        run: axis(s.deep),
        u: axis(s.long),
        v: axis(s.wide),
        interval: s.through,
        boundary,
        closed: true,
        caps: [Some(false); 2],
    }
    .gap(p, tip)
}

fn issue(code: &str, name: &str, view: &str, tip: P, message: String, why: &str) -> LintIssue {
    LintIssue {
        sheet: None,
        id: format!("l:{code}:{name}"),
        code: code.into(),
        severity: if matches!(
            code,
            "radius_leader_target_unverifiable"
                | "step_diameter_leader_target_unverifiable"
                | "chamfer_leader_target_unverifiable"
                | "flat_leader_target_unverifiable"
                | "recess_leader_target_unverifiable"
                | "slot_leader_target_unverifiable"
                | "step_length_witness_target_unverifiable"
        ) {
            Severity::Info
        } else {
            Severity::Warning
        },
        message,
        at: Some(tip),
        view: Some(view.into()),
        subjects: Vec::new(),
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: Some(name.into()),
        related: Vec::new(),
        evidence: Some(why.into()),
        fix: None,
    }
}

/// The size parameters a chamfer callout states for its bevels.
const CHAMFER_SIZES: &[&str] = &[
    "chamfer.length",
    "chamfer.angle",
    "oriented_chamfer.length",
    "oriented_chamfer.angle",
];

/// A callout's claims that its drawn geometry does not support: the claims of *annotation* on
/// *features* for *parameters* earn no coverage credit ([`super::lint`] removes them from its
/// mark before the ledgers read it), since its leader or witness is off the physical
/// feature (ISSUE-1374-a, ISSUE-1374-d).
#[derive(Clone, Debug, PartialEq)]
pub(super) struct Withdrawn {
    pub annotation: String,
    pub features: Vec<String>,
    pub parameters: &'static [&'static str],
}

/// Every finding of this module on *drawing*, and the claims its callouts lose.
pub(super) fn check(analysis: &Analysis, drawing: &Drawing) -> (Vec<LintIssue>, Vec<Withdrawn>) {
    let read: BTreeMap<&str, Hole> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect();
    let tips_shafts: BTreeMap<&str, [P; 2]> = drawing
        .sheet
        .items
        .iter()
        .filter(|d| d.kind == Kind::Dimension && d.elbow.is_some())
        .filter_map(|d| Some((d.name.as_str(), *d.lines.first()?)))
        .collect();
    let mut issues = Vec::new();
    let mut withdrawn = Vec::new();
    witnesses(analysis, drawing, &mut issues, &mut withdrawn);
    for a in &drawing.annotations {
        let (Some(view), Some(&[tip, elbow])) =
            (a.view.as_deref(), tips_shafts.get(a.name.as_str()))
        else {
            continue;
        };
        let Some(p) = drawing
            .sheet
            .frames
            .get(view.strip_prefix("v:").unwrap_or(view))
            .and_then(|f| Projection::new(analysis, f))
        else {
            continue;
        };
        let claimed = |parameter: &str| -> Vec<&str> {
            a.claims
                .iter()
                .filter(|c| c.parameter == parameter)
                .map(|c| c.feature.as_str())
                .collect()
        };
        let mut found =
            |code: &str, subjects: Vec<&str>, parameter: &str, message: String, why: &str| {
                let mut i = issue(code, &a.name, view, tip, message, why);
                i.measurements = subjects
                    .iter()
                    .map(|f| format!("{f}/{parameter}"))
                    .collect();
                i.subjects = subjects.into_iter().map(String::from).collect();
                issues.push(i);
            };
        let bores = claimed("bore.diameter");
        if !bores.is_empty() {
            let mut gaps: Vec<f64> = bores
                .iter()
                .filter_map(|f| read.get(f))
                .flat_map(rims)
                .map(|(c, n, r)| p.circle_gap(c, n, r, tip))
                .collect();
            for f in analysis
                .features
                .iter()
                .filter(|f| f.kind == "double_d_bore" && bores.contains(&f.id.as_str()))
            {
                if let Ok(Some(gap)) = double_d_gap(analysis, &p, f, tip) {
                    gaps.push(gap);
                }
            }
            let gap = gaps.iter().copied().fold(f64::INFINITY, f64::min);
            if gaps.is_empty() {
                found(
                    "diameter_leader_target_unverifiable",
                    bores,
                    "bore.diameter",
                    format!(
                        "{}: the diameter leader's physical boundary cannot be verified",
                        a.name
                    ),
                    "no hole the callout names has a readable boundary in this view",
                );
            } else if gap > TOLERANCE {
                found(
                    "diameter_leader_target_mismatch",
                    bores,
                    "bore.diameter",
                    format!(
                        "{}: the diameter leader tip does not touch its named physical boundary (it lies {gap:.3} mm from the nearest rim of the holes it names)",
                        a.name
                    ),
                    "drawn tip is outside the named physical boundary tolerance",
                );
            }
        }
        // Read the drawn shaft, so authored/pinned leaders receive the same check.
        // A consolidated fillet names its own faces, rather than borrowing a blend target.
        let fillets = claimed("fillet.radius");
        let is_fillet = !fillets.is_empty();
        let profiles = claimed("cylindrical_profile.radius");
        let (radii, parameter) = if !fillets.is_empty() {
            (fillets, "fillet.radius")
        } else if !profiles.is_empty() {
            (profiles, "cylindrical_profile.radius")
        } else {
            (claimed("blend.radius"), "blend.radius")
        };
        if !radii.is_empty() {
            let mut shown = Vec::new();
            let mut reasons = Vec::new();
            for id in &radii {
                match crate::radius::projected(analysis, id, p.view.toward, |q| p.page(q)) {
                    Ok(arcs) => shown.extend(arcs),
                    Err(why) => reasons.push(why),
                }
            }
            reasons.sort();
            reasons.dedup();
            if shown.is_empty() {
                found(
                    "radius_leader_target_unverifiable",
                    radii,
                    parameter,
                    format!(
                        "{}: the radius leader's trimmed profile arc cannot be verified",
                        a.name
                    ),
                    &reasons.join("; "),
                );
            } else if !shown.iter().any(|arc| arc.on_arc(tip)) {
                found(
                    if is_fillet {
                        "radius_leader_not_radial"
                    } else {
                        "radius_leader_target_mismatch"
                    },
                    radii,
                    parameter,
                    format!(
                        "{}: the radius leader tip does not touch its named trimmed profile arc",
                        a.name
                    ),
                    "drawn tip is outside the trimmed physical profile arc tolerance",
                );
            } else if !shown.iter().any(|arc| arc.accepts([tip, elbow])) {
                let mut why: Vec<_> = shown
                    .iter()
                    .filter(|arc| arc.on_arc(tip))
                    .filter_map(|arc| arc.violation([tip, elbow]))
                    .collect();
                why.sort();
                why.dedup();
                found(
                    "radius_leader_not_radial",
                    radii,
                    parameter,
                    format!(
                        "{}: radius leader must touch the middle 25–75% of the arc and approach within 15 degrees of radial from either side; automatic routing keeps the drawn route when no compliant candidate fits",
                        a.name
                    ),
                    &why.join("; "),
                );
            }
        }
        for parameter in ["circular_step_radius.radius", "slot_end_radius.radius"] {
            let steps = claimed(parameter);
            if !steps.is_empty() {
                let arcs: Vec<_> = steps
                    .iter()
                    .filter_map(|id| analysis.profile_arcs.get(*id))
                    .flatten()
                    .collect();
                let on = arcs.iter().any(|arc| {
                    let (c, r, span) = arc.projected(|q| p.page(q));
                    ((tip[0] - c[0]).hypot(tip[1] - c[1]) - r).abs() <= TOLERANCE
                        && span.contains(
                            (tip[1] - c[1]).atan2(tip[0] - c[0]),
                            TOLERANCE / r.max(1e-9),
                        )
                });
                if !on {
                    found(
                        if arcs.is_empty() {
                            "radius_leader_target_unverifiable"
                        } else {
                            "radius_leader_target_mismatch"
                        },
                        steps,
                        parameter,
                        format!(
                            "{}: the radius leader tip does not touch its named trimmed circular profile arc",
                            a.name
                        ),
                        "drawn tip must be on a recorded circular boundary edge, within its angular span",
                    );
                }
            }
        }
        // A turned step's diameter, a chamfer's size, and a flat's, recess's or slot's
        // callout, against the faces they name.
        let kind = |id: &str| {
            analysis
                .features
                .iter()
                .find(|f| f.id == id)
                .map_or("", |f| f.kind.as_str())
        };
        let by_kind = |kinds: &[&str], skip: &[&str]| -> (Vec<&str>, Option<&str>) {
            let mut ids: Vec<&str> = Vec::new();
            let mut first = None;
            for c in &a.claims {
                let k = kind(&c.feature);
                if !kinds.contains(&k) || skip.contains(&c.parameter.as_str()) {
                    continue;
                }
                first.get_or_insert(c.parameter.as_str());
                // A slot pattern's callout is on its member slots' walls.
                let members: Vec<&str> = if k == "slot_pattern" {
                    analysis
                        .features
                        .iter()
                        .find(|f| f.id == c.feature)
                        .map(|f| {
                            super::slots::members(analysis, f)
                                .into_iter()
                                .map(|m| m.id.as_str())
                                .collect()
                        })
                        .unwrap_or_default()
                } else {
                    vec![c.feature.as_str()]
                };
                for m in members {
                    if !ids.contains(&m) {
                        ids.push(m);
                    }
                }
            }
            (ids, first)
        };
        type Gap = fn(&Analysis, &Projection, &Feature, P) -> Result<f64, String>;
        let mut families: Vec<(&str, Vec<&str>, Option<&str>, Gap)> = Vec::new();
        for (noun, parameters, gap) in [
            ("step_diameter", &["step.diameter"][..], step_gap as Gap),
            (
                "chamfer",
                &["chamfer.length", "oriented_chamfer.length"][..],
                chamfer_gap as Gap,
            ),
        ] {
            let ids: Vec<&str> = parameters.iter().flat_map(|p| claimed(p)).collect();
            let first = parameters.iter().find(|p| !claimed(p).is_empty()).copied();
            families.push((noun, ids, first, gap));
        }
        // A flat's length callout, whatever else of the flat the leader also claims.
        let mut ids: Vec<&str> = Vec::new();
        for f in claimed("flat.length") {
            if kind(f) == "flat" && !ids.contains(&f) {
                ids.push(f);
            }
        }
        let first = (!ids.is_empty()).then_some("flat.length");
        families.push(("flat", ids, first, flat_gap));
        let (ids, first) = by_kind(&["section_recess"], &[]);
        families.push(("recess", ids, first, recess_gap));
        // An end radius is judged against its arcs above.
        let (ids, first) = by_kind(
            &["slot", "slot_pattern", "oriented_slot"],
            &["slot_end_radius.radius"],
        );
        families.push(("slot", ids, first, slot_gap));
        for (noun, ids, parameter, gap) in families {
            // One callout may count members of several of these families (principal and
            // oriented chamfers): its tip need only meet one member's face.
            let Some(parameter) = parameter else {
                continue;
            };
            if ids.is_empty() {
                continue;
            }
            let (mut gaps, mut reasons) = (Vec::new(), Vec::new());
            for f in analysis
                .features
                .iter()
                .filter(|f| ids.contains(&f.id.as_str()))
            {
                match gap(analysis, &p, f, tip) {
                    Ok(g) => gaps.push(g),
                    Err(why) => reasons.push(why),
                }
            }
            reasons.sort();
            reasons.dedup();
            let best = gaps.iter().copied().fold(f64::INFINITY, f64::min);
            if best <= TOLERANCE {
                continue;
            }
            let leader = match noun {
                "step_diameter" => "diameter",
                n => n,
            };
            // Credit for the size goes with the leader (ISSUE-1374-a/d): a tip proven off
            // every member's face, or a view that cannot show any member's bevel, states
            // nothing for them.
            let withdraw = |out: &mut Vec<Withdrawn>| {
                if let Some(parameters) = match noun {
                    "chamfer" => Some(CHAMFER_SIZES),
                    "step_diameter" => Some(&["step.diameter"][..]),
                    _ => None,
                } {
                    out.push(Withdrawn {
                        annotation: a.name.clone(),
                        features: ids.iter().map(|s| s.to_string()).collect(),
                        parameters,
                    });
                }
            };
            // A miss is proven only when every claimed member could be measured: the tip
            // may sit on a member this view cannot verify.
            if !reasons.is_empty() {
                if gaps.is_empty() && reasons.iter().all(|r| r.starts_with("the view ")) {
                    withdraw(&mut withdrawn);
                }
                // Literal codes, so the finding-impact source inventory sees each one.
                found(
                    match noun {
                        "chamfer" => "chamfer_leader_target_unverifiable",
                        "flat" => "flat_leader_target_unverifiable",
                        "recess" => "recess_leader_target_unverifiable",
                        "slot" => "slot_leader_target_unverifiable",
                        _ => "step_diameter_leader_target_unverifiable",
                    },
                    ids,
                    parameter,
                    format!(
                        "{}: the {leader} leader's target cannot be verified in this view",
                        a.name
                    ),
                    &reasons.join("; "),
                );
            } else {
                withdraw(&mut withdrawn);
                let what = match noun {
                    "step_diameter" => {
                        "diameter leader tip does not touch its named turned surface"
                    }
                    "chamfer" => "chamfer leader tip does not touch its named bevel",
                    "flat" => "flat leader tip does not touch its named flat face",
                    "recess" => {
                        "recess leader tip does not touch its named recess's walls, floor or capped ends"
                    }
                    _ => "slot leader tip does not touch its named slot's walls",
                };
                found(
                    match noun {
                        "chamfer" => "chamfer_leader_target_mismatch",
                        "flat" => "flat_leader_target_mismatch",
                        "recess" => "recess_leader_target_mismatch",
                        "slot" => "slot_leader_target_mismatch",
                        _ => "step_diameter_leader_target_mismatch",
                    },
                    ids,
                    parameter,
                    format!("{}: the {what} (it lies {best:.3} mm from it)", a.name),
                    "drawn tip is off the named physical surface's projected outline",
                );
            }
        }
        // The shaft against the material it left at its tip.
        let material = crate::material::solids(analysis, |q| p.page(q));
        let shaft = tips_shafts[a.name.as_str()];
        let cut = crate::material::reentry(&material, shaft);
        if cut > 0.0 {
            let mut i = issue(
                "leader_crosses_silhouette",
                &a.name,
                view,
                shaft[1],
                format!(
                    "leader '{}' shaft cuts {cut:.1} mm back through view '{}' — route it clear of the body, or dimension the feature in a detail view",
                    a.name,
                    view.strip_prefix("v:").unwrap_or(view)
                ),
                "the shaft re-enters the part's material after leaving it at its tip",
            );
            i.severity = Severity::Info;
            i.id = format!("l:leader_crosses_silhouette:{}", a.name);
            issues.push(i);
        }
    }
    (issues, withdrawn)
}

/// ISSUE-1374-d's witness clause: a linear dimension claiming a turned step's `step.length` has
/// its extension lines at the band's own shoulders, as its view projects them: the stations its
/// witnesses meet the dimension line at are the band's ends along its axis (one member's, for a
/// grouped length). Off every claimed band's span is `step_length_witness_target_mismatch`, and
/// the dimension states none of their lengths; a view along the axis, or a record that cannot
/// be read, is `…_unverifiable` (info).
fn witnesses(
    analysis: &Analysis,
    drawing: &Drawing,
    issues: &mut Vec<LintIssue>,
    withdrawn: &mut Vec<Withdrawn>,
) {
    let frame = holes::frame(analysis);
    for a in &drawing.annotations {
        let ids: Vec<&str> = a
            .claims
            .iter()
            .filter(|c| c.parameter == "step.length")
            .map(|c| c.feature.as_str())
            .collect();
        let Some(view) = a.view.as_deref() else {
            continue;
        };
        if ids.is_empty() {
            continue;
        }
        let Some(ink) = drawing
            .sheet
            .items
            .iter()
            .filter(|d| d.name == a.name && d.kind == Kind::Dimension && d.elbow.is_none())
            .find_map(|d| crate::output::linear_ink(&d.lines))
        else {
            continue;
        };
        let Some(p) = drawing
            .sheet
            .frames
            .get(view.strip_prefix("v:").unwrap_or(view))
            .and_then(|f| Projection::new(analysis, f))
        else {
            continue;
        };
        let k = ink.k;
        let (mut gaps, mut reasons) = (Vec::new(), Vec::new());
        for f in analysis
            .features
            .iter()
            .filter(|f| ids.contains(&f.id.as_str()))
        {
            let measured = super::turned::band(&frame, f).and_then(|b| {
                if p.end_on(axis(b.axis)) {
                    return Err("the view looks along its axis".to_string());
                }
                let end = |s: f64| {
                    let mut q = b.on;
                    q[b.axis] = s;
                    p.page(q)
                };
                let (q0, q1) = (end(b.lo), end(b.hi));
                if (q0[1 - k] - q1[1 - k]).abs() > TOLERANCE {
                    return Err("its axis does not run along the dimension".to_string());
                }
                let (s0, s1) = (q0[k].min(q1[k]), q0[k].max(q1[k]));
                Ok((ink.span[0] - s0).abs().max((ink.span[1] - s1).abs()))
            });
            match measured {
                Ok(g) => gaps.push(g),
                Err(why) => reasons.push(why),
            }
        }
        reasons.sort();
        reasons.dedup();
        let best = gaps.iter().copied().fold(f64::INFINITY, f64::min);
        if best <= TOLERANCE {
            continue;
        }
        let tip = [ink.span[0], ink.offset];
        let tip = if k == 0 { tip } else { [tip[1], tip[0]] };
        let (code, message, why) = if reasons.is_empty() {
            withdrawn.push(Withdrawn {
                annotation: a.name.clone(),
                features: ids.iter().map(|s| s.to_string()).collect(),
                parameters: &["step.length"],
            });
            (
                "step_length_witness_target_mismatch",
                format!(
                    "{}: the length's extension lines are not at its named turned band's shoulders (they lie {best:.3} mm off them)",
                    a.name
                ),
                "drawn witness stations are off the named band's projected span".to_string(),
            )
        } else {
            (
                "step_length_witness_target_unverifiable",
                format!(
                    "{}: the length's witness stations cannot be verified in this view",
                    a.name
                ),
                reasons.join("; "),
            )
        };
        let mut i = issue(code, &a.name, view, tip, message, &why);
        i.measurements = ids.iter().map(|f| format!("{f}/step.length")).collect();
        i.subjects = ids.iter().map(|s| s.to_string()).collect();
        issues.push(i);
    }
}
