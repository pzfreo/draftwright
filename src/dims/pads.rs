//! Raised pads (quiddity's `RaisedPad`, the Analysis's `pad` features), dimensioned as
//! draftwright's sheets state them (ISSUE-885-a, ISSUE-909-a: `PadFeature.parameters` in
//! `model/ir.py`, the footprint corridors of `compose.py` and `pad_height_jobs` in
//! `annotations/_pocket_pad.py`), each pad on its own: equal pads at distinct sites keep their
//! own statements (only one span, one dimension merges them, [`super::share`]).
//!
//! Each pad is read once, through lint's reader ([`crate::lint::pads::placed`]), so the plan and
//! lint agree on its footprint axes (draftwright's `_PAD_PLANE_AXES`: the first its length, the
//! second its width) and its locations ([`crate::lint::pads::locations`]). In the view that looks
//! down on it (along the axis it stands on):
//!
//! - **its footprint**: `pad_width.length` and `pad_length.length`, linear dimensions between its
//!   own edges;
//! - **its height** from the face it stands on, `pad_height.length`, a callout `5 HIGH` led to the
//!   middle of a footprint edge (draftwright's `_pad_height_label`; a face level measures the
//!   body from the datum, not the pad's own height);
//! - **its location** from the part's corner (draftwright's `datum_xy`) to its centre, in both
//!   directions across it: `location_pad.location.x` and `.y` for a pad standing along z,
//!   `location_pad.<axis>` otherwise, a direction in which its centre is on the corner left out
//!   with an info `location_on_datum` (draftwright's `pad_location_coincident_with_datum`).
//!
//! A pad whose record cannot be read in the part's frame is `feature_not_dimensioned`.

use super::recesses::{location, on_datum};
use super::{AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, want_id};
use crate::lint::pads::{locations, placed};

pub(super) const PLANNER: Planner = Planner {
    kinds: &["pad"],
    wants,
};

/// draftwright's footprint axes for a pad standing along each axis: its length, then its width.
const PLANE_AXES: [[usize; 2]; 3] = [[1, 2], [2, 0], [0, 1]];

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let a = cx.analysis;
    let [lo, hi] = a.envelope;
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for f in a.features.iter().filter(|f| f.kind == "pad") {
        let p = match placed(a, f) {
            Ok(p) => p,
            Err(why) => {
                problems.push(Problem {
                    id: format!("p:feature_not_dimensioned:{}", f.id),
                    code: "feature_not_dimensioned".into(),
                    severity: "warning".into(),
                    message: format!(
                        "{} is not dimensioned: {why}.",
                        crate::wording::capital(&crate::wording::feature(f))
                    ),
                    subjects: vec![f.id.clone()],
                    ..Default::default()
                });
                continue;
            }
        };
        let axis = p.axis;
        let view = end_on(axis);
        let b = p.bounds;
        let centre: P3 = b.map(|r| 0.5 * (r[0] + r[1]));
        // Its free end: the end of its span farther from the middle of the part's box.
        let mid = 0.5 * (lo[axis] + hi[axis]);
        let top = if (b[axis][1] - mid).abs() >= (b[axis][0] - mid).abs() {
            b[axis][1]
        } else {
            b[axis][0]
        };
        let mut face = centre;
        face[axis] = top;
        let [long, wide] = PLANE_AXES[axis];
        for (parameter, k, word) in [
            ("pad_width.length", wide, "width"),
            ("pad_length.length", long, "length"),
        ] {
            let v = b[k][1] - b[k][0];
            let (mut from, mut to) = (face, face);
            from[k] = b[k][0];
            to[k] = b[k][1];
            out.push(Want { units: cx.spec.units,
                id: want_id(&f.id, parameter),
                views: vec![view],
                value: v,
                terms: vec![fmt(v)],
                measurements: vec![Measurement {
                    feature: f.id.clone(),
                    parameter: parameter.into(),
                    value: v,
                }],
                turning: None,
                basis: format!(
                    "a raised pad's footprint {word} along {}, between its own edges, in the view that looks down on it",
                    AXES[k]
                ),
                shape: Shape::Linear {
                    a: from,
                    b: to,
                    axis: k,
                    a_edge: false,
                    b_edge: false,
                    order: 0,
                },
            });
        }
        let height = b[axis][1] - b[axis][0];
        let edge = |k: usize, end: usize| {
            let mut c = face;
            c[k] = b[k][end];
            (c, 0.0)
        };
        out.push(Want { units: cx.spec.units,
            id: want_id(&f.id, "pad_height.length"),
            views: vec![view],
            value: height,
            terms: vec![fmt(height), "HIGH".into()],
            measurements: vec![Measurement {
                feature: f.id.clone(),
                parameter: "pad_height.length".into(),
                value: height,
            }],
            turning: None,
            basis: format!(
                "a raised pad's height along {} from the face it stands on, a callout led to its footprint, in the view that looks down on it",
                AXES[axis]
            ),
            shape: Shape::Callout { arcs: Vec::new(),
                sites: vec![
                    edge(long, 1),
                    edge(long, 0),
                    edge(wide, 1),
                    edge(wide, 0),
                ],
            },
        });
        let stated = locations(&p, lo);
        let directions = if axis == 2 { [0, 1] } else { PLANE_AXES[axis] };
        for k in directions {
            let Some((parameter, _)) = stated
                .iter()
                .find(|(s, _)| s.rsplit('.').next() == Some(AXES[k]))
            else {
                problems.push(on_datum(a, &f.id, k));
                continue;
            };
            out.push(location(
                &[f],
                parameter,
                face,
                k,
                lo[k],
                view,
                format!(
                    "a raised pad located in {} from the part's {} face to its centre, in the view that looks down on it",
                    AXES[k],
                    ["left", "front", "bottom"][k]
                ),
             cx.spec.units));
        }
    }
    (out, problems)
}
