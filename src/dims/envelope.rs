//! The overall extents of a part that is not turned whole: its width, depth and height, each
//! stated once, outermost, in the first view that shows it. A part turned whole states its
//! diameter and length instead ([`super::turned`]), and whole-part polygonal stock its size across
//! flats and its length ([`super::stock`]).

use super::{AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, fmt, want_id};

pub(super) const PLANNER: Planner = Planner { kinds: &[], wants };

/// The overall extents' names, by world axis.
pub(super) const NAMES: [&str; 3] = ["width", "depth", "height"];

/// The views that show world axis *axis*, preferred first.
pub(super) fn profile_views(axis: usize) -> Vec<&'static str> {
    match axis {
        0 => vec!["front", "plan"],
        1 => vec!["side", "plan"],
        _ => vec!["front", "side"],
    }
}

/// *other* with its coordinate *k* moved to *at*.
pub(super) fn corner(k: usize, at: f64, other: P3) -> P3 {
    let mut p = other;
    p[k] = at;
    p
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    if cx.turned.is_some() || super::stock::whole(cx) || crate::springs::whole(cx.analysis) {
        return (Vec::new(), Vec::new());
    }
    let [lo, hi] = cx.analysis.envelope;
    let mut out = Vec::new();
    for (k, name) in NAMES.iter().enumerate() {
        let v = hi[k] - lo[k];
        if v <= 1e-6 {
            continue;
        }
        let views = profile_views(k);
        out.push(Want {
            id: want_id("envelope", &format!("{name}.length")),
            views: views.clone(),
            value: v,
            terms: vec![fmt(v)],
            measurements: vec![Measurement {
                feature: "envelope".into(),
                parameter: format!("{name}.length"),
                value: v,
            }],
            turning: None,
            basis: format!(
                "every overall extent is stated once, outermost: the {name} ({} extent) in the {} view",
                AXES[k], views[0]
            ),
            shape: Shape::Linear {
                a: corner(k, lo[k], lo),
                b: corner(k, hi[k], lo),
                axis: k,
                a_edge: true,
                b_edge: true,
                order: 2,
            },
        });
    }
    (out, Vec::new())
}
