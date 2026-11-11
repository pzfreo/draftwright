//! Planar steps: rectangular through steps (quiddity's `ThroughStep`), circular blind steps
//! (`CircularBlindStep`) and paired-ramp steps (`PairedRampStep`), each dimensioned in the view
//! that looks along its run, as draftwright's `render_through_steps`, `render_circular_blind_steps`
//! and `render_paired_ramp_steps` (`annotations/from_model.py`) do:
//!
//! - a **through step** states both legs of its open section (`through_step_leg.length.<axis>`,
//!   named by the part's axis each lies along), each a linear dimension between its ends; its run
//!   is the overall extent's. A step running along x or y whose legs the height ladder, a plate
//!   and the overall extents already state is theirs, as in draftwright ([`super::levels`]); it is
//!   reported (`dimensions_not_ported`, info), since those dimensions do not claim it;
//! - a **circular blind step** (a quarter cylinder stopped short of one end) is one callout,
//!   `R4 × 25 DEEP`: its radius and its blind depth (`circular_step_radius.radius`,
//!   `circular_step_depth.length`), with a leader to its arc;
//! - a **paired-ramp step** (a V of two equal ramps) is one callout, `2× 51.3° × 25 RUN`: the
//!   ramps' angle and their run (`ramp_angle.angle`, `ramp_run.length`), with a leader to the
//!   ramps' ridge.
//!
//! A record that cannot be read in the part's frame (no principal run, a section that is not two
//! legs, a radius, depth, angle or run that is not a positive size) is reported
//! (`feature_not_dimensioned`), never dropped. Angled and oblique through steps stay undrafted,
//! as in draftwright ([`super::UNDRAFTED`]).

use serde_json::Value;

use super::levels::{Through, ownership};
use super::{AXES, Context, Measurement, Planner, Problem, QFrame, Shape, Want, end_on, point};
use crate::bridge::Feature;
use crate::bridge::holes::principal;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["through_step", "circular_blind_step", "paired_ramp_step"],
    wants,
};

fn not_dimensioned(f: &Feature, why: &str) -> Problem {
    Problem {
        id: format!("p:feature_not_dimensioned:{}", f.id),
        code: "feature_not_dimensioned".into(),
        severity: "warning".into(),
        message: format!(
            "{} is not dimensioned: {why}.",
            crate::wording::capital(&crate::wording::feature(f))
        ),
        subjects: vec![f.id.clone()],
        ..Default::default()
    }
}

/// A record's size *name*, if it is a positive finite number.
fn size(f: &Feature, name: &str) -> Result<f64, String> {
    match f.parameters.get(name).and_then(Value::as_f64) {
        Some(v) if v.is_finite() && v > 0.0 => Ok(v),
        Some(v) => Err(format!("its {name} {v} is not a positive size")),
        None => Err(format!("its record gives no {name}")),
    }
}

/// The world axis a record's run `axis` lies along.
fn run(f: &Feature, frame: &QFrame) -> Result<usize, String> {
    let letter = f.parameters.get("axis").ok_or("its record gives no axis")?;
    frame
        .letter(letter)
        .and_then(principal)
        .ok_or_else(|| "its run lies along no axis of the part".into())
}

/// A through step's two legs, each in the view that looks along its run.
fn through_wants(s: &Through, units: crate::units::Units) -> Vec<Want> {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let view = end_on(s.run);
    let id = &s.feature.id;
    s.legs()
        .into_iter()
        .map(|(k, a, b)| {
            let value = (b[k] - a[k]).abs();
            let parameter = format!("through_step_leg.length.{}", AXES[k]);
            Want { units,
                id: super::want_id(id, &parameter),
                views: vec![view],
                value,
                terms: vec![fmt(value)],
                measurements: vec![Measurement {
                    feature: id.clone(),
                    parameter,
                    value,
                }],
                basis: format!(
                    "a rectangular through step states both legs of its open section, in the view that looks along its run: this leg runs along {}",
                    AXES[k]
                ),
                shape: Shape::Linear {
                    a,
                    b,
                    axis: k,
                    a_edge: false,
                    b_edge: false,
                    order: 0,
                },
                turning: None,
            }
        })
        .collect()
}

/// A circular blind step's callout: `R<radius> × <depth> DEEP`, with a leader to its arc.
fn circular(
    f: &Feature,
    frame: &QFrame,
    analysis: &crate::model::Analysis,
    units: crate::units::Units,
) -> Result<Want, String> {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let r = size(f, "radius")?;
    let depth = size(f, "length")?;
    let k = run(f, frame)?;
    let ends: Vec<_> = f
        .parameters
        .get("centreline")
        .and_then(Value::as_array)
        .ok_or("its record gives no centreline")?
        .iter()
        .map(|p| point(Some(p)))
        .collect::<Option<_>>()
        .ok_or("its centreline is not points")?;
    let [a, b] = ends[..] else {
        return Err("its centreline is not two points".into());
    };
    let centre = frame.point([0, 1, 2].map(|j| 0.5 * (a[j] + b[j])));
    let view = end_on(k);
    let terms = vec![
        format!("R{}", fmt(r)),
        "×".into(),
        fmt(depth),
        "DEEP".into(),
    ];
    Ok(Want { units,
        id: super::want_id(&f.id, "circular_step_radius.radius"),
        views: vec![view],
        value: r,
        terms,
        measurements: vec![
            Measurement {
                feature: f.id.clone(),
                parameter: "circular_step_radius.radius".into(),
                value: r,
            },
            Measurement {
                feature: f.id.clone(),
                parameter: "circular_step_depth.length".into(),
                value: depth,
            },
        ],
        basis: "a circular blind step states its radius and blind depth in one callout, in the view that sees its arc".into(),
        shape: Shape::Callout {
            arcs: analysis.profile_arcs.get(&f.id).cloned().ok_or("its trimmed circular boundary is unavailable")?,
            sites: vec![(centre, r)],
        },
        turning: None,
    })
}

/// A paired-ramp step's callout: `2× <angle>° × <run> RUN`, with a leader to the ramps' ridge.
fn ramp(f: &Feature, frame: &QFrame, units: crate::units::Units) -> Result<Want, String> {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let angle = size(f, "angle")?;
    if angle >= 90.0 {
        return Err(format!("its angle {angle} is not acute"));
    }
    let length = size(f, "length")?;
    let k = run(f, frame)?;
    let at = point(f.parameters.get("at")).ok_or("its record gives no at")?;
    let view = end_on(k);
    let terms = vec![
        "2×".into(),
        format!("{}°", crate::dims::fmt(angle)),
        "×".into(),
        fmt(length),
        "RUN".into(),
    ];
    Ok(Want { units,
        id: super::want_id(&f.id, "ramp_angle.angle"),
        views: vec![view],
        value: angle,
        terms,
        measurements: vec![
            Measurement {
                feature: f.id.clone(),
                parameter: "ramp_angle.angle".into(),
                value: angle,
            },
            Measurement {
                feature: f.id.clone(),
                parameter: "ramp_run.length".into(),
                value: length,
            },
        ],
        basis: "a paired-ramp step states its two equal ramp angles and their run in one callout, in the view that sees the ramps' section".into(),
        shape: Shape::Callout { arcs: Vec::new(),
            sites: vec![(frame.point(at), 0.0)],
        },
        turning: None,
    })
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut out = Vec::new();
    let mut problems = Vec::new();
    // Unreadable through steps are reported by the ladder's ownership stage, which reads them.
    let o = ownership(cx);
    for s in &o.lowered {
        out.extend(through_wants(s, cx.spec.units));
    }
    for (s, owners) in &o.legacy {
        let f = s.feature;
        // The owners are named by their plates' IDs, so two plates of one thickness stay
        // apart; the message names a plate's thickness by its size (#24).
        let owners: Vec<String> = owners
            .iter()
            .map(|o| match o.strip_suffix("'s thickness") {
                Some(plate) => {
                    let p = cx.analysis.features.iter().find(|f| f.id == plate);
                    let size = |name: &str| p.and_then(|p| p.parameters.get(name)?.as_f64());
                    match size("hi").zip(size("lo")) {
                        Some((hi, lo)) => format!("the {} plate thickness", fmt(hi - lo)),
                        None => "a plate's thickness".into(),
                    }
                }
                None => o.clone(),
            })
            .collect();
        let named = match owners.split_last() {
            Some((last, [])) => last.clone(),
            Some((last, rest)) => format!("{} and {last}", rest.join(", ")),
            None => String::new(),
        };
        problems.push(Problem {
            id: format!("p:dimensions_not_ported:{}", f.id),
            code: "dimensions_not_ported".into(),
            severity: "info".into(),
            message: format!(
                "{} is not dimensioned as a through step: as in draftwright, its legs are stated by {named}, but those dimensions do not claim it.",
                crate::wording::capital(&crate::wording::feature(f))
            ),
            subjects: vec![f.id.clone()],
            ..Default::default()
        });
    }
    for f in &cx.analysis.features {
        let want = match f.kind.as_str() {
            "circular_blind_step" => circular(f, &cx.frame, cx.analysis, cx.spec.units),
            "paired_ramp_step" => ramp(f, &cx.frame, cx.spec.units),
            _ => continue,
        };
        match want {
            Ok(w) => out.push(w),
            Err(why) => problems.push(not_dimensioned(f, &why)),
        }
    }
    (out, problems)
}
