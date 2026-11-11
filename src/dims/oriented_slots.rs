//! Free-direction slots (quiddity's `OrientedSlot`, the Analysis's `oriented_slot` features):
//! one callout carrying both requirements, `SLOT 2 WIDE × 4.5 LONG`
//! (LINT-oriented_slot_coverage-width_length, ISSUE-1432-a; draftwright's `render_oriented_slots`
//! and `_oriented_slot_label` in `annotations/from_model.py`, without its internal `ORIENTED`,
//! #225). A gap open at both ends ([`crate::lint::oriented_slots::open`], a retaining ring's) has
//! no ends to be long between: `GAP 1.64 WIDE`, its length the wall it cuts.
//!
//! - **Its view** looks along the slot's dominant run axis (draftwright's `_END_ON` of the slot
//!   frame's axis, the run direction's largest component).
//! - **Its tip** is on the slot's physical rim: its outline across its depth
//!   ([`crate::lint::oriented_slots::passage`], from the record's origin and measuring axes, or
//!   its proved passage section) at the end of its depth nearer the viewer
//!   (`_oriented_slot_rim_polygon`), its edges' midpoints the leader's candidate targets, so a
//!   rotated slot's leader lands on its rotated rim, not on an axis-aligned box.
//! - Equal slots seen in one view (one width and length) share one counted callout,
//!   `2× SLOT …`, claiming every member. draftwright leaves the members of oriented slot
//!   patterns undrawn (#1430: patterns have no grammar there); the Analysis does not record the
//!   patterns (the bridge reads their members as oriented slots), so their sizes are stated here
//!   through the counted callout, never left unstated.
//!
//! A record whose sizes, frame or section cannot be read is `feature_not_dimensioned`.

use std::collections::BTreeMap;

use serde_json::Value;

use super::{Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, key, want_id};
use crate::bridge::Feature;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["oriented_slot"],
    wants,
};

/// A slot read in the part's frame: its width and length, its dominant run axis, and its rim's
/// edge midpoints at the run's end nearer the viewer of that axis's view.
struct Read {
    width: f64,
    length: f64,
    open: bool,
    axis: usize,
    rim: Vec<P3>,
}

fn read(analysis: &crate::model::Analysis, f: &Feature) -> Result<Read, String> {
    let p = &f.parameters;
    let size = |name: &str| {
        p.get(name)
            .and_then(Value::as_f64)
            .filter(|v| v.is_finite() && *v > 0.0)
            .ok_or_else(|| format!("its record gives no positive {name}"))
    };
    let (width, length) = (size("width")?, size("length")?);
    let s = crate::lint::oriented_slots::passage(analysis, f)?;
    let axis = (0..3)
        .max_by(|&i, &j| s.run[i].abs().total_cmp(&s.run[j].abs()))
        // Invariant: The iterator is over the fixed three coordinate axes.
        .expect("three axes");
    let toward = super::TOWARD
        .iter()
        .find(|(n, _)| *n == end_on(axis))
        .map(|(_, t)| *t)
        // Invariant: The fixed principal view table includes one view along each coordinate axis.
        .expect("a view along each axis");
    let facing = (0..3).map(|k| s.run[k] * toward[k]).sum::<f64>() > 0.0;
    let [lo, hi] = s.interval;
    let station = if facing { hi.max(lo) } else { lo.min(hi) };
    let at = |c: [f64; 2]| -> P3 {
        [0, 1, 2].map(|k| s.origin[k] + station * s.run[k] + c[0] * s.u[k] + c[1] * s.v[k])
    };
    let corners: Vec<P3> = s.boundary.iter().map(|(c, _)| at(*c)).collect();
    let rim = (0..corners.len())
        .map(|i| {
            let (a, b) = (corners[i], corners[(i + 1) % corners.len()]);
            [0, 1, 2].map(|k| 0.5 * (a[k] + b[k]))
        })
        .collect();
    Ok(Read {
        width,
        length,
        open: crate::lint::oriented_slots::open(f),
        axis,
        rim,
    })
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut problems = Vec::new();
    // Slots by view axis, width and length, in the Analysis's order of their first member.
    type Key = (usize, i64, i64, bool);
    let mut groups: BTreeMap<Key, Vec<(&Feature, Read)>> = BTreeMap::new();
    let mut order: Vec<Key> = Vec::new();
    for f in cx
        .analysis
        .features
        .iter()
        .filter(|f| f.kind == "oriented_slot")
    {
        match read(cx.analysis, f) {
            Ok(r) => {
                let k = (r.axis, key(r.width), key(r.length), r.open);
                if !groups.contains_key(&k) {
                    order.push(k);
                }
                groups.entry(k).or_default().push((f, r));
            }
            Err(why) => problems.push(Problem {
                id: format!("p:feature_not_dimensioned:{}", f.id),
                code: "feature_not_dimensioned".into(),
                severity: "warning".into(),
                message: format!(
                    "{} is not dimensioned: {why}.",
                    crate::wording::capital(&crate::wording::feature(f))
                ),
                subjects: vec![f.id.clone()],
                ..Default::default()
            }),
        }
    }
    let mut out = Vec::new();
    for k in order {
        let members = &groups[&k];
        let (first, r0) = &members[0];
        let n = members.len();
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        terms.extend([
            if r0.open { "GAP" } else { "SLOT" }.into(),
            fmt(r0.width),
            "WIDE".into(),
        ]);
        if !r0.open {
            terms.extend(["×".into(), fmt(r0.length), "LONG".into()]);
        }
        out.push(Want { units: cx.spec.units,
            id: want_id(&first.id, "oriented_slot_width.length"),
            views: vec![end_on(r0.axis)],
            value: r0.width,
            terms,
            measurements: members
                .iter()
                .flat_map(|(f, r)| {
                    [
                        ("oriented_slot_width.length", r.width),
                        ("oriented_slot_length.length", r.length),
                    ]
                    .into_iter()
                    .take(if r.open { 1 } else { 2 })
                    .map(|(parameter, value)| Measurement {
                        feature: f.id.clone(),
                        parameter: parameter.into(),
                        value,
                    })
                })
                .collect(),
            turning: None,
            basis: if r0.open {
                "a gap open at both ends states its width alone (its length is the wall it cuts), in the view along its run, the leader to its rim".into()
            } else if n > 1 {
                format!(
                    "{n} free-direction slots of one width and length are called out once and counted, in the view along their run, the leader to a rim"
                )
            } else {
                "a free-direction slot's width and length in one callout, in the view along its run, the leader to its rim".into()
            },
            shape: Shape::Callout { arcs: Vec::new(),
                sites: members
                    .iter()
                    .flat_map(|(_, r)| r.rim.iter().map(|p| (*p, 0.0)))
                    .collect(),
            },
        });
    }
    (out, problems)
}
