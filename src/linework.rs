//! Visible edges take precedence over hidden edges in each placed view (ISO 128-2).
//! Work in page mm, preserving the exact curves when a hidden stretch is split.

use crate::output::{Curve, Item, P};

pub const PRECEDENCE: f64 = 0.15;
const CHORD: f64 = 0.001;
const DUPLICATE: f64 = 0.004;

fn visible_tolerance() -> f64 {
    crate::output::layer_styles()[0].1.width / 2.0 + PRECEDENCE + 2.0 * CHORD
}

/// An ellipse thinner than SVG's endpoint rounding can acquire a false return
/// arc on export. Its straight support retains the major-axis extrema, within
/// 1e-6 page mm, without retracing that support twice.
pub fn stable_curve(curve: Curve) -> Curve {
    let Curve::Conic {
        radii,
        x_axis,
        y_axis,
        t0,
        t1,
        ..
    } = &curve
    else {
        return curve;
    };
    if radii[0].min(radii[1]) > 1e-6 {
        return curve;
    }
    let shift = if radii[0] >= radii[1] {
        0.0
    } else {
        std::f64::consts::FRAC_PI_2
    };
    let mut parameters = vec![0.0, 1.0];
    let (lo, hi) = (t0.min(*t1), t0.max(*t1));
    let pi = std::f64::consts::PI;
    for k in ((lo - shift) / pi).ceil() as i64..=((hi - shift) / pi).floor() as i64 {
        let u = (shift + k as f64 * pi - t0) / (t1 - t0);
        if u > 0.0 && u < 1.0 {
            parameters.push(u);
        }
    }
    let axis = if radii[0] >= radii[1] {
        *x_axis
    } else {
        *y_axis
    };
    let mut points: Vec<P> = parameters.into_iter().map(|u| curve.at(u)).collect();
    points.sort_by(|a, b| dot(*a, axis).total_cmp(&dot(*b, axis)));
    // Invariant: The preceding guard requires at least two points.
    let (a, b) = (points[0], *points.last().unwrap());
    if dot(curve.start(), axis) > dot(curve.end(), axis) {
        Curve::Line(b, a)
    } else {
        Curve::Line(a, b)
    }
}

type Segment = (P, P);
fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}
fn dot(a: P, b: P) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}
fn distance(a: P, b: P) -> f64 {
    let d = sub(a, b);
    d[0].hypot(d[1])
}

/// Parameterised chords, refined on quarters as well as the midpoint so an inflection
/// cannot masquerade as a straight line. Piece boundaries are always sampled.
fn chords(curve: &Curve) -> Vec<(f64, P)> {
    fn refine(c: &Curve, lo: f64, hi: f64, out: &mut Vec<(f64, P)>, depth: usize) {
        let (a, b) = (c.at(lo), c.at(hi));
        let flat = [0.25, 0.5, 0.75].into_iter().all(|t| {
            distance(
                c.at(lo + t * (hi - lo)),
                [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])],
            ) <= CHORD
        });
        if flat || depth == 24 {
            out.push((hi, b));
        } else {
            let mid = (lo + hi) / 2.0;
            refine(c, lo, mid, out, depth + 1);
            refine(c, mid, hi, out, depth + 1);
        }
    }
    let n = match curve {
        Curve::Conic { t0, t1, .. } => {
            ((t1 - t0).abs() / std::f64::consts::FRAC_PI_2).ceil() as usize
        }
        Curve::Beziers(p) => p.len(),
        Curve::Rational(p) => p.len(),
        Curve::Line(..) => 1,
    }
    .max(1);
    let mut out = vec![(0.0, curve.start())];
    for i in 0..n {
        refine(
            curve,
            i as f64 / n as f64,
            (i + 1) as f64 / n as f64,
            &mut out,
            0,
        );
    }
    out
}

fn merge(mut intervals: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut out: Vec<(f64, f64)> = Vec::new();
    for (lo, hi) in intervals {
        let (lo, hi) = (lo.max(0.0), hi.min(1.0));
        if hi <= lo {
            continue;
        }
        if let Some(last) = out.last_mut()
            && lo <= last.1 + 1e-10
        {
            last.1 = last.1.max(hi);
        } else {
            out.push((lo, hi));
        }
    }
    out
}

/// The part of chord a–b inside the round-ended tolerance band of c–d.
fn capsule((a, b): Segment, (c, d): Segment, tolerance: f64) -> Vec<(f64, f64)> {
    if (0..2).any(|k| {
        a[k].max(b[k]) < c[k].min(d[k]) - tolerance || a[k].min(b[k]) > c[k].max(d[k]) + tolerance
    }) {
        return Vec::new();
    }
    let v = sub(b, a);
    let w = sub(d, c);
    let ww = dot(w, w);
    let mut out = Vec::new();
    let clip = |value: f64, slope: f64, min: f64, max: f64, range: &mut (f64, f64)| {
        if slope.abs() < 1e-15 {
            if value < min || value > max {
                range.1 = range.0;
            }
        } else {
            let (u, v) = ((min - value) / slope, (max - value) / slope);
            range.0 = range.0.max(u.min(v));
            range.1 = range.1.min(u.max(v));
        }
    };
    if ww > 1e-20 {
        let ac = sub(a, c);
        let mut range = (0.0, 1.0);
        clip(dot(ac, w), dot(v, w), 0.0, ww, &mut range);
        let cross = |p: P| p[0] * w[1] - p[1] * w[0];
        let band = tolerance * ww.sqrt();
        clip(cross(ac), cross(v), -band, band, &mut range);
        if range.1 > range.0 {
            out.push(range);
        }
    }
    let vv = dot(v, v);
    if vv > 1e-20 {
        for end in [c, d] {
            let q = sub(a, end);
            let mid = -dot(q, v) / vv;
            let discriminant = tolerance * tolerance - dot(q, q) + dot(q, v).powi(2) / vv;
            if discriminant >= 0.0 {
                let half = (discriminant / vv).sqrt();
                out.push((mid - half, mid + half));
            }
        }
    }
    merge(out)
}

struct Ink {
    segments: Vec<Segment>,
    grid: std::collections::HashMap<(i64, i64), Vec<usize>>,
}
fn cells((a, b): Segment, tolerance: f64) -> impl Iterator<Item = (i64, i64)> {
    let bounds = [0, 1].map(|k| {
        (
            ((a[k].min(b[k]) - tolerance) / 5.0).floor() as i64,
            ((a[k].max(b[k]) + tolerance) / 5.0).floor() as i64,
        )
    });
    (bounds[0].0..=bounds[0].1).flat_map(move |x| (bounds[1].0..=bounds[1].1).map(move |y| (x, y)))
}
impl Ink {
    fn new() -> Self {
        Self {
            segments: Vec::new(),
            grid: std::collections::HashMap::new(),
        }
    }
    fn add(&mut self, curve: &Curve, tolerance: f64) {
        for pair in chords(curve).windows(2) {
            let segment = (pair[0].1, pair[1].1);
            let index = self.segments.len();
            self.segments.push(segment);
            for cell in cells(segment, tolerance) {
                self.grid.entry(cell).or_default().push(index);
            }
        }
    }
    fn covered(&self, curve: &Curve, tolerance: f64) -> Vec<(f64, f64)> {
        let mut intervals = Vec::new();
        for pair in chords(curve).windows(2) {
            let (u, a) = pair[0];
            let (v, b) = pair[1];
            let mut candidates: Vec<usize> = cells((a, b), 0.0)
                .filter_map(|cell| self.grid.get(&cell))
                .flatten()
                .copied()
                .collect();
            candidates.sort_unstable();
            candidates.dedup();
            for i in candidates {
                intervals.extend(
                    capsule((a, b), self.segments[i], tolerance)
                        .into_iter()
                        .map(|(lo, hi)| (u + lo * (v - u), u + hi * (v - u))),
                );
            }
        }
        merge(intervals)
    }
}

fn remaining(curve: &Curve, covered: Vec<(f64, f64)>) -> Vec<Curve> {
    let mut start = 0.0;
    let mut out = Vec::new();
    for (lo, hi) in covered.into_iter().chain([(1.0, 1.0)]) {
        if lo > start + 1e-9 {
            out.push(curve.trim(start, lo));
        }
        start = hi;
    }
    out
}

/// Subtract visible curves from one hidden curve, retaining exact arc and spline pieces.
pub fn subtract(curve: &Curve, visible: &[Curve]) -> Vec<Curve> {
    let mut ink = Ink::new();
    for c in visible {
        ink.add(c, visible_tolerance());
    }
    remaining(curve, ink.covered(curve, visible_tolerance()))
}

fn straight(curve: Curve) -> Curve {
    if let Curve::Beziers(pieces) = &curve {
        let a = curve.start();
        let Some(b) = pieces
            .iter()
            .flatten()
            .copied()
            .find(|p| distance(a, *p) > 1e-9)
        else {
            return curve;
        };
        let v = sub(b, a);
        let length = distance(a, b);
        let along = |p: P| dot(sub(p, a), v) / length;
        if pieces.iter().all(|p| p.len() == 2)
            && pieces.iter().flatten().all(|p| {
                let q = sub(*p, a);
                (q[0] * v[1] - q[1] * v[0]).abs() / length < 1e-8
            })
        {
            // Tessellated edge-on curves can retrace their support. Its union is the
            // exact interval between extrema, regardless of the source traversal.
            let mut points: Vec<P> = pieces.iter().flatten().copied().collect();
            points.sort_by(|p, q| along(*p).total_cmp(&along(*q)));
            // Invariant: The preceding guard requires at least two points.
            let (lo, hi) = (points[0], *points.last().unwrap());
            return if along(curve.end()) >= along(curve.start()) {
                Curve::Line(lo, hi)
            } else {
                Curve::Line(hi, lo)
            };
        }
    }
    curve
}

fn reversed(curve: &Curve) -> Curve {
    let mut out = curve.clone();
    match &mut out {
        Curve::Line(a, b) => std::mem::swap(a, b),
        Curve::Conic { t0, t1, .. } => std::mem::swap(t0, t1),
        Curve::Beziers(pieces) => {
            pieces.reverse();
            pieces.iter_mut().for_each(|p| p.reverse());
        }
        Curve::Rational(pieces) => {
            pieces.reverse();
            pieces.iter_mut().for_each(|p| p.reverse());
        }
    }
    out
}

fn joined(a: &Curve, b: &Curve) -> Option<Curve> {
    let v = sub(a.end(), a.at(1.0 - 1e-5));
    let w = sub(b.at(1e-5), b.start());
    if dot(v, w) <= 0.0
        || (v[0] * w[1] - v[1] * w[0]).abs() > 0.001 * dot(v, v).sqrt() * dot(w, w).sqrt()
    {
        return None;
    }
    match (a, b) {
        (Curve::Line(start, end), Curve::Line(_, last)) => {
            let v = sub(*end, *start);
            let q = sub(*last, *start);
            let across = (q[0] * v[1] - q[1] * v[0]).abs() / distance(*start, *end);
            if across < 1e-6 {
                Some(Curve::Line(*start, *last))
            } else if across <= DUPLICATE {
                joined(&Curve::Beziers(vec![vec![*start, *end]]), b)
            } else {
                None
            }
        }
        (
            Curve::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                t1,
            },
            Curve::Conic {
                centre: c,
                radii: r,
                x_axis: x,
                y_axis: y,
                t0: lo,
                t1: hi,
            },
        ) if centre == c && radii == r && x_axis == x && y_axis == y => Some(Curve::Conic {
            centre: *centre,
            radii: *radii,
            x_axis: *x_axis,
            y_axis: *y_axis,
            t0: *t0,
            t1: hi + ((t1 - lo) / std::f64::consts::TAU).round() * std::f64::consts::TAU,
        }),
        (Curve::Line(start, end), Curve::Beziers(_)) => {
            joined(&Curve::Beziers(vec![vec![*start, *end]]), b)
        }
        (Curve::Beziers(_), Curve::Line(start, end)) => {
            joined(a, &Curve::Beziers(vec![vec![*start, *end]]))
        }
        (Curve::Beziers(p), Curve::Beziers(q)) => {
            let mut pieces = p.clone();
            if distance(a.end(), b.start()) > 1e-9 {
                pieces.push(vec![a.end(), b.start()]);
            }
            pieces.extend(q.iter().cloned());
            Some(Curve::Beziers(pieces))
        }
        (Curve::Rational(p), Curve::Rational(q)) => {
            let mut pieces = p.clone();
            if distance(a.end(), b.start()) > 1e-9 {
                pieces.push([a.end(), b.start()].map(|p| [p[0], p[1], 1.0]).to_vec());
            }
            pieces.extend(q.iter().cloned());
            Some(Curve::Rational(pieces))
        }
        _ => None,
    }
}

/// Join touching curves before deduplication can trim their shared endpoints. Index the
/// original endpoints so long tessellated chains do not require repeated all-pairs scans.
fn touching(curves: Vec<Curve>, tolerance: f64) -> Vec<Curve> {
    let cell = |p: P| {
        (
            (p[0] / tolerance).floor() as i64,
            (p[1] / tolerance).floor() as i64,
        )
    };
    let mut ends = std::collections::BTreeMap::<_, Vec<usize>>::new();
    for (i, c) in curves.iter().enumerate() {
        if distance(c.start(), c.end()) <= 1e-7 {
            continue;
        }
        for p in [c.start(), c.end()] {
            ends.entry(cell(p)).or_default().push(i);
        }
    }
    let mut used = vec![false; curves.len()];
    let mut out = Vec::new();
    for (i, c) in curves.iter().enumerate() {
        if used[i] {
            continue;
        }
        used[i] = true;
        let mut run = c.clone();
        loop {
            if distance(run.start(), run.end()) <= 1e-7 {
                break;
            }
            let mut next = None;
            for prepend in [false, true] {
                let p = if prepend { run.start() } else { run.end() };
                let (x, y) = cell(p);
                let mut candidates = Vec::new();
                for x in x - 1..=x + 1 {
                    for y in y - 1..=y + 1 {
                        if let Some(indices) = ends.get(&(x, y)) {
                            candidates.extend(indices.iter().copied().filter(|&j| !used[j]));
                        }
                    }
                }
                candidates.sort_unstable();
                candidates.dedup();
                for j in candidates {
                    for reverse in [false, true] {
                        let candidate = if reverse {
                            reversed(&curves[j])
                        } else {
                            curves[j].clone()
                        };
                        let q = if prepend {
                            candidate.end()
                        } else {
                            candidate.start()
                        };
                        if distance(p, q) <= tolerance {
                            let merged = if prepend {
                                joined(&candidate, &run)
                            } else {
                                joined(&run, &candidate)
                            };
                            if let Some(merged) = merged {
                                next = Some((j, merged));
                                break;
                            }
                        }
                    }
                    if next.is_some() {
                        break;
                    }
                }
                if next.is_some() {
                    break;
                }
            }
            let Some((j, merged)) = next else { break };
            used[j] = true;
            run = merged;
        }
        out.push(run);
    }
    out
}

/// Apply line precedence and draw overlapping hidden pieces once. Keep long pieces first,
/// then merge collinear fragments so overlapping pieces cannot fill one another's dash gaps.
pub fn hidden(visible: &[Item], hidden: Vec<Item>) -> Vec<Item> {
    let mut ink = Ink::new();
    for item in visible {
        if let Item::Edge(c) = item {
            ink.add(c, visible_tolerance());
        }
    }
    let curves: Vec<Curve> = hidden
        .into_iter()
        .filter_map(|i| match i {
            Item::Edge(c) => Some(straight(c)),
            _ => None,
        })
        .collect();
    let mut curves: Vec<(f64, Curve)> = touching(curves, 1e-7)
        .into_iter()
        .map(|c| {
            let length = chords(&c)
                .windows(2)
                .map(|w| distance(w[0].1, w[1].1))
                .sum();
            (length, c)
        })
        .collect();
    curves.sort_by(|a, b| b.0.total_cmp(&a.0));
    let mut unique = Ink::new();
    let mut out = Vec::new();
    for (_, c) in curves {
        for c in remaining(&c, ink.covered(&c, visible_tolerance())) {
            for piece in remaining(&c, unique.covered(&c, DUPLICATE)) {
                unique.add(&piece, DUPLICATE);
                out.push(piece);
            }
        }
    }
    // Dedup can trim both ends of a join by DUPLICATE. Restore that gap on a shared
    // straight support; visible subtraction creates much larger gaps.
    let mut i = 0;
    while i < out.len() {
        let mut j = i + 1;
        while j < out.len() {
            if let (Curve::Line(a, b), Curve::Line(c, d)) = (&out[i], &out[j]) {
                let v = sub(*b, *a);
                let length = distance(*a, *b);
                let along = |p: P| dot(sub(p, *a), v) / length;
                let across = |p: P| {
                    let q = sub(p, *a);
                    (q[0] * v[1] - q[1] * v[0]).abs() / length
                };
                let (lo, hi) = (along(*c).min(along(*d)), along(*c).max(along(*d)));
                if across(*c) < 1e-6
                    && across(*d) < 1e-6
                    && lo <= length + 2.0 * DUPLICATE + 1e-8
                    && hi >= -2.0 * DUPLICATE - 1e-8
                {
                    let at = |t: f64| [a[0] + v[0] * t / length, a[1] + v[1] * t / length];
                    out[i] = Curve::Line(at(lo.min(0.0)), at(hi.max(length)));
                    out.remove(j);
                    j = i + 1;
                    continue;
                }
            }
            j += 1;
        }
        i += 1;
    }
    // Restore curve joins removed by duplicate capsule trims, including chord error
    // on each side. The visible precedence gap is far larger than this allowance.
    touching(out, 2.0 * (DUPLICATE + CHORD))
        .into_iter()
        .map(Item::Edge)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;
    #[test]
    fn nearly_edge_on_arcs_cannot_double_back_after_svg_rounding() {
        let curve = Curve::Conic {
            centre: [150., 220.],
            radii: [2., 4e-9],
            x_axis: [1., 0.],
            y_axis: [0., 1.],
            t0: 0.2,
            t1: 1.4,
        };
        let stable = stable_curve(curve.clone());
        assert!(matches!(stable, Curve::Line(..)));
        assert_eq!(stable.start(), curve.start());
        assert_eq!(stable.end(), curve.end());
        let full = stable_curve(Curve::Conic {
            centre: [150., 220.],
            radii: [2., 4e-9],
            x_axis: [1., 0.],
            y_axis: [0., 1.],
            t0: 0.,
            t1: 2. * PI,
        });
        assert!((full.bbox()[0] - 148.).abs() < 1e-9 && (full.bbox()[2] - 152.).abs() < 1e-9);
    }
    #[test]
    fn partial_and_reversed_line_overlap() {
        let h = Curve::Line([0., 0.], [10., 0.]);
        let result = subtract(&h, &[Curve::Line([7., 0.], [3., 0.])]);
        assert_eq!(result.len(), 2);
        assert!(result[0].end()[0] < 3. && result[1].start()[0] > 7.);
        assert!(subtract(&h, &[Curve::Line([10., 0.], [0., 0.])]).is_empty());
    }
    #[test]
    fn arcs_are_split_and_kept_exact() {
        let arc = |t0, t1| Curve::Conic {
            centre: [0., 0.],
            radii: [5., 5.],
            x_axis: [1., 0.],
            y_axis: [0., 1.],
            t0,
            t1,
        };
        let result = subtract(&arc(0., PI), &[arc(PI / 3., 2. * PI / 3.)]);
        assert_eq!(result.len(), 2);
        assert!(result.iter().all(|c| matches!(c, Curve::Conic { .. })));
        assert!(subtract(&arc(0., PI), &[arc(PI, 0.)]).is_empty());
    }
    #[test]
    fn distinct_edges_survive_and_overlapping_dashes_are_one_run() {
        let line = |a, b, y| Item::Edge(Curve::Line([a, y], [b, y]));
        let result = hidden(
            &[],
            vec![
                line(0., 6., 0.),
                line(10., 4., 0.),
                line(0., 6., 0.),
                line(0., 10., 0.1),
            ],
        );
        assert_eq!(result.len(), 2);
        assert!(result.contains(&line(0., 10., 0.)) || result.contains(&line(10., 0., 0.)));
    }
    #[test]
    fn touching_short_lines_keep_one_dash_run() {
        let pieces = (0..80)
            .rev()
            .map(|i| {
                let (a, b) = (
                    [140.368, 20. + i as f64 * 0.38],
                    [140.368, 20. + (i + 1) as f64 * 0.38],
                );
                Item::Edge(if i % 2 == 0 {
                    Curve::Line(a, b)
                } else {
                    Curve::Beziers(vec![vec![b, a]])
                })
            })
            .collect();
        let result = hidden(&[], pieces);
        assert_eq!(
            result.len(),
            1,
            "short touching pieces restart the dash phase"
        );
        let Item::Edge(c) = &result[0] else {
            panic!("edge")
        };
        assert!((distance(c.start(), c.end()) - 30.4).abs() < 1e-8);
        let retraced = Curve::Beziers(vec![
            vec![[0., 0.], [2., 0.]],
            vec![[2., 0.], [0., 0.]],
            vec![[0., 0.], [1., 0.]],
        ]);
        assert_eq!(
            hidden(&[], vec![Item::Edge(retraced)]),
            vec![Item::Edge(Curve::Line([0., 0.], [2., 0.]))]
        );
    }
    #[test]
    fn touching_curved_pieces_keep_exact_geometry_and_one_dash_run() {
        let arc = Curve::Conic {
            centre: [0., 0.],
            radii: [5., 3.],
            x_axis: [1., 0.],
            y_axis: [0., 1.],
            t0: 0.,
            t1: PI,
        };
        let circle = Curve::Conic {
            centre: [0., 0.],
            radii: [5., 3.],
            x_axis: [1., 0.],
            y_axis: [0., 1.],
            t0: 0.,
            t1: 2. * PI,
        };
        let duplicate_circle = hidden(
            &[],
            vec![Item::Edge(circle.clone()), Item::Edge(circle.clone())],
        );
        assert_eq!(duplicate_circle, vec![Item::Edge(circle)]);
        let spline = Curve::Beziers(vec![vec![[0., 0.], [2., 4.], [8., 4.], [10., 0.]]]);
        let rational = Curve::Rational(vec![vec![
            [0., 0., 1.],
            [2., 4., 0.8],
            [8., 4., 0.8],
            [10., 0., 1.],
        ]]);
        for curve in [arc, spline, rational] {
            let pieces = (0..32)
                .map(|i| {
                    let piece = curve.trim(i as f64 / 32., (i + 1) as f64 / 32.);
                    Item::Edge(if i % 2 == 0 { piece } else { reversed(&piece) })
                })
                .collect();
            let result = hidden(&[], pieces);
            assert_eq!(
                result.len(),
                1,
                "touching curve pieces restart the dash phase"
            );
            let Item::Edge(joined) = &result[0] else {
                panic!("edge")
            };
            for i in 0..=128 {
                let u = i as f64 / 128.;
                assert!(distance(joined.at(u), curve.at(u)) < 1e-8);
            }
        }
    }
    #[test]
    fn partial_splines_remain_exact() {
        let curve = Curve::Beziers(vec![vec![[0., 0.], [2., 4.], [8., 4.], [10., 0.]]]);
        let middle = curve.trim(0.25, 0.75);
        let result = subtract(&curve, &[middle]);
        assert_eq!(result.len(), 2);
        assert!(result.iter().all(|c| matches!(c, Curve::Beziers(_))));
    }
}

/// Optional authored line-work switches. No geometry or projected edge identity enters Spec.
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hidden: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub centre_marks: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub centre_lines: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thread_depiction: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub knurl_depiction: Option<bool>,
}

impl Settings {
    pub fn is_empty(&self) -> bool {
        self == &Self::default()
    }
}

/// Resolve explicit intent once per view. Sections retain their independent drawing rules.
pub(crate) fn configure(spec: &crate::model::Spec, views: &mut [crate::model::PlannedView]) {
    if spec.sheet.hidden.is_none()
        && spec.sheet.centre_marks.is_none()
        && spec.sheet.centre_lines.is_none()
        && spec.sheet.thread_depiction.is_none()
        && spec.sheet.knurl_depiction.is_none()
        && spec.views.values().all(|v| {
            v.hidden.is_none()
                && v.centre_marks.is_none()
                && v.centre_lines.is_none()
                && v.thread_depiction.is_none()
                && v.knurl_depiction.is_none()
        })
    {
        return;
    }
    for v in views {
        let name = v.id.trim_start_matches("v:");
        let entry = spec.views.get(name);
        let mut settings = Settings::default();
        let mut paths = Vec::new();
        macro_rules! setting {
            ($field:ident) => {
                if let Some(value) = entry.and_then(|e| e.$field) {
                    settings.$field = Some(value);
                    paths.push(format!("views/{name}/{}", stringify!($field)));
                } else if let Some(value) = spec.sheet.$field {
                    settings.$field = Some(value);
                    paths.push(format!("sheet/{}", stringify!($field)));
                }
            };
        }
        setting!(hidden);
        setting!(centre_marks);
        setting!(centre_lines);
        setting!(thread_depiction);
        setting!(knurl_depiction);
        if settings.is_empty() {
            continue;
        }
        v.line_style = settings;
        v.from_spec
            .retain(|p| !p.starts_with("sheet/") && !p.starts_with(&format!("views/{name}/")));
        v.from_spec.extend(paths);
        v.default = false;
        let basis = v
            .basis
            .split("; Spec line work: ")
            .next()
            // Invariant: str::split always yields at least one segment, even for empty text.
            .unwrap()
            .to_owned();
        v.basis = format!(
            "{basis}; Spec line work: {} (sections retain no hidden or cosmetic ink)",
            v.from_spec.join(", ")
        );
    }
}

/// Spec/editor vocabulary shared by sheet and view switches.
pub const FIELDS: [&str; 5] = [
    "hidden",
    "centre_marks",
    "centre_lines",
    "thread_depiction",
    "knurl_depiction",
];
