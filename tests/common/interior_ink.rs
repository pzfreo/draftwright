//! Independent oracle over the *rendered* drawing: an interior annotation's label, lines and
//! arrowheads clear every unrelated drawn edge (visible and hidden, from the renderer's own
//! hidden-line projection) and all section furniture. Source-edge continuations and contacts
//! are checked against the actual visible edge; planner provenance alone excuses nothing.
#![allow(dead_code)]
use draftwright::dims::PlannedDimension;
use draftwright::output::{Curve, Drawing, Item, Kind};
type P = [f64; 2];

fn distance(p: P, [a, b]: [P; 2]) -> f64 {
    let d = [b[0] - a[0], b[1] - a[1]];
    let n = d[0] * d[0] + d[1] * d[1];
    let t = if n == 0.0 {
        0.0
    } else {
        ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / n
    }
    .clamp(0.0, 1.0);
    (p[0] - a[0] - t * d[0]).hypot(p[1] - a[1] - t * d[1])
}

fn meet(s: [P; 2], t: [P; 2], gap: f64) -> bool {
    let cross = |a: P, b: P, c: P| (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    let a = cross(s[0], s[1], t[0]);
    let b = cross(s[0], s[1], t[1]);
    let c = cross(t[0], t[1], s[0]);
    let d = cross(t[0], t[1], s[1]);
    (a * b < 0.0 && c * d < 0.0)
        || s.iter().any(|p| distance(*p, t) < gap)
        || t.iter().any(|p| distance(*p, s) < gap)
}

fn segments(c: &Curve) -> Vec<[P; 2]> {
    if let Curve::Line(a, b) = c {
        return vec![[*a, *b]];
    }
    let b = c.bbox();
    let n = ((b[2] - b[0]).hypot(b[3] - b[1]) * 20.0).ceil().max(32.0) as usize;
    (0..n)
        .map(|i| [c.at(i as f64 / n as f64), c.at((i + 1) as f64 / n as f64)])
        .collect()
}

fn layer(drawing: &Drawing, name: &str) -> Vec<[P; 2]> {
    drawing
        .layers
        .iter()
        .filter(|l| l.name == name)
        .flat_map(|l| &l.items)
        .flat_map(|i| match i {
            Item::Edge(c) | Item::TextStroke(c, _) | Item::FrameText(c, _) => segments(c),
            Item::Face(loops) | Item::Glyph(loops, _) => {
                loops.iter().flatten().flat_map(segments).collect()
            }
        })
        .collect()
}

fn overlaps(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
}

/// An outline parallel to the tip's own edge and within 3 mm of the tip (a chamfer band around
/// a hex's flats), crossed by the leader at more than 20 degrees: any leader leaving the tip
/// crosses it, inside the view or out.
fn banded(visible: &[[P; 2]], [tip, elbow]: [P; 2], e: [P; 2]) -> bool {
    let unit = |[a, b]: [P; 2]| {
        let d = [b[0] - a[0], b[1] - a[1]];
        let n = d[0].hypot(d[1]);
        (n > 1e-12).then(|| [d[0] / n, d[1] / n])
    };
    let cross = |a: P, b: P| a[0] * b[1] - a[1] * b[0];
    let Some(own) = visible
        .iter()
        .filter(|s| distance(tip, **s) < 0.15)
        .find_map(|s| unit(*s))
    else {
        return false;
    };
    let (Some(leader), Some(d)) = (unit([tip, elbow]), unit(e)) else {
        return false;
    };
    cross(own, leader).abs() >= 20f64.to_radians().sin()
        && cross(own, d).abs() < 2f64.to_radians().sin()
        && cross([e[0][0] - tip[0], e[0][1] - tip[1]], own).abs() <= 3.0
}

/// Linear extensions may continue their actual visible support edge; arrow
/// tips may touch that edge. Every other rendered edge remains an obstacle.
fn linear_edge_clear(visible: &[[P; 2]], s: [P; 2], e: [P; 2], witness: bool, tips: &[P]) -> bool {
    if !visible.contains(&e) {
        return !meet(s, e, 0.1);
    }
    let closest = |p: P| {
        visible
            .iter()
            .map(|e| distance(p, *e))
            .fold(f64::INFINITY, f64::min)
    };
    let unit = |s: [P; 2]| {
        let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
        let n = d[0].hypot(d[1]);
        (n > 1e-9).then(|| [d[0] / n, d[1] / n])
    };
    if witness
        && closest(s[0]) < 0.01
        && distance(s[0], e) <= closest(s[0]) + 1e-6
        && unit(s)
            .zip(unit(e))
            .is_some_and(|(u, v)| (u[0] * v[1] - u[1] * v[0]).abs() < 1e-5)
    {
        return true;
    }
    let delta = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
    let n = delta[0].hypot(delta[1]);
    if n < 1e-9 {
        return !meet(s, e, 0.1);
    }
    let mut collars = Vec::new();
    for &tip in tips {
        let nearest = closest(tip);
        if nearest < 0.15 && distance(tip, e) <= nearest + 1e-6 && distance(tip, s) < 1e-6 {
            let t = ((tip[0] - s[0][0]) * delta[0] + (tip[1] - s[0][1]) * delta[1]) / (n * n);
            collars.push(((t - 0.6 / n).max(0.), (t + 0.6 / n).min(1.)));
        }
    }
    collars.sort_by(|a, b| a.0.total_cmp(&b.0));
    let at = |t: f64| [s[0][0] + t * delta[0], s[0][1] + t * delta[1]];
    let mut start = 0.;
    for (lo, hi) in collars.into_iter().chain(std::iter::once((1., 1.))) {
        if lo > start && meet([at(start), at(lo)], e, 0.1) {
            return false;
        }
        start = start.max(hi);
    }
    true
}

#[test]
fn linear_support_exceptions_never_forgive_a_neighbouring_edge() {
    let support = [[0., -10.], [0., 10.]];
    let neighbour = [[0.3, -10.], [1.3, 10.]];
    let visible = [support, neighbour];
    let shaft = [[0., 0.], [4., 0.]];
    assert!(linear_edge_clear(
        &visible,
        shaft,
        support,
        false,
        &[[0., 0.]]
    ));
    assert!(!linear_edge_clear(
        &visible,
        shaft,
        neighbour,
        false,
        &[[0., 0.]]
    ));
    assert!(linear_edge_clear(
        &visible,
        [[0., 1.], [0., 4.]],
        support,
        true,
        &[]
    ));
    let short = [[0., 0.], [0., 2.]];
    let separate = [[0., 4.], [0., 6.]];
    assert!(!linear_edge_clear(
        &[short, separate],
        [[0., 1.], [0., 5.]],
        separate,
        true,
        &[]
    ));
}

pub fn assert_clear(drawing: &Drawing, d: &PlannedDimension) {
    let p = d.placed.as_ref().unwrap();
    let visible = layer(drawing, "part");
    let mut edges = visible.clone();
    edges.extend(layer(drawing, "hidden"));
    let furniture: Vec<[f64; 4]> = drawing
        .sheet
        .items
        .iter()
        .filter(|i| matches!(i.kind, Kind::Hatch | Kind::CuttingPlane | Kind::Caption))
        .map(|i| i.ink)
        .collect();
    let callout = d.kind == "callout";
    if callout {
        assert!(
            visible.iter().any(|s| distance(p.lines[0][0], *s) < 0.15),
            "{} tip misses every visible edge",
            d.id
        );
    }
    let b = p.label_box;
    let label = [b[0] - 0.3, b[1] - 0.3, b[2] + 0.3, b[3] + 0.3];
    let corners = [
        [label[0], label[1]],
        [label[2], label[1]],
        [label[2], label[3]],
        [label[0], label[3]],
    ];
    for e in &edges {
        let inside =
            |q: P| q[0] > label[0] && q[0] < label[2] && q[1] > label[1] && q[1] < label[3];
        assert!(
            !inside(e[0])
                && !inside(e[1])
                && !(0..4).any(|k| meet(*e, [corners[k], corners[(k + 1) % 4]], 0.0)),
            "{} label lies on drawn line-work",
            d.id
        );
    }
    assert!(
        !furniture.iter().any(|f| overlaps(label, *f)),
        "{} label lies on section furniture",
        d.id
    );
    let mut strokes: Vec<_> = p
        .lines
        .iter()
        .enumerate()
        .map(|(i, s)| (*s, callout && i == 0))
        .collect();
    for [tip, tail] in &p.arrows {
        let delta = [tail[0] - tip[0], tail[1] - tip[1]];
        let length = delta[0].hypot(delta[1]).max(1e-12);
        let k = draftwright::dims::ARROW / 6.0 / length;
        let a = [tail[0] - k * delta[1], tail[1] + k * delta[0]];
        let b = [tail[0] + k * delta[1], tail[1] - k * delta[0]];
        strokes.extend([
            ([*tip, *tail], callout),
            ([*tip, a], callout),
            ([*tip, b], callout),
            ([a, b], false),
        ]);
    }
    for (i, (s, attachment)) in strokes.iter().enumerate() {
        let mut s = *s;
        if *attachment {
            let n = (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]);
            let t = (0.6 / n.max(1e-12)).min(1.0);
            s[0] = [
                s[0][0] + t * (s[1][0] - s[0][0]),
                s[0][1] + t * (s[1][1] - s[0][1]),
            ];
        }
        assert!(
            !edges.iter().any(|e| {
                if d.kind == "linear" && p.arrows.len() == 2 && p.lines.len() >= 3 {
                    return !linear_edge_clear(
                        &visible,
                        s,
                        *e,
                        i < 2,
                        &p.arrows.iter().map(|a| a[0]).collect::<Vec<_>>(),
                    );
                }
                meet(s, *e, 0.1) && !(*attachment && banded(&visible, p.lines[0], *e))
            }),
            "{} line/arrow {i} crosses drawn line-work",
            d.id
        );
    }
}
