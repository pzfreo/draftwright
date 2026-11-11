//! Double-D bores: the major diameter and across flats form one indivisible callout.
//! The leader lands on an uncut circular arc, even when the flats are rotated.
use super::{Context, Measurement, Planner, Problem, Shape, Want, end_on, point, want_id};
use crate::bridge::holes::{dir_to_part, frame, principal, to_part};

pub(super) const PLANNER: Planner = Planner {
    kinds: &["double_d_bore"],
    wants,
};

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut out = Vec::new();
    let mut problems = Vec::new();
    let fr = frame(cx.analysis);
    for f in cx
        .analysis
        .features
        .iter()
        .filter(|f| f.kind == "double_d_bore")
    {
        let read = || -> Result<Want, String> {
            let p = &f.parameters;
            let size = |name: &str| {
                p.get(name)
                    .and_then(|v| v.as_f64())
                    .filter(|v| v.is_finite() && *v > 0.)
                    .ok_or_else(|| format!("its record gives no positive {name}"))
            };
            let major = size("major_diameter")?;
            let across = size("across_flats")?;
            if across >= major {
                return Err("across flats must be less than the major diameter".into());
            }
            let axis = dir_to_part(&fr, point(p.get("axis")).ok_or("its record gives no axis")?);
            let k = principal(axis).ok_or("no principal view shows its profile true")?;
            let through = p
                .get("through")
                .and_then(|v| v.as_bool())
                .ok_or("its record gives no through fact")?;
            let centre = to_part(
                &fr,
                point(p.get("location")).ok_or("its record gives no location")?,
            );
            let flat = dir_to_part(
                &fr,
                point(p.get("flat_direction")).ok_or("its record gives no flat direction")?,
            );
            let n = flat.iter().map(|x| x * x).sum::<f64>().sqrt();
            if !n.is_finite() || n < 1e-9 || flat[k].abs() / n > 1e-6 {
                return Err(
                    "its flat direction is not a finite line perpendicular to the bore".into(),
                );
            }
            // A flat direction is an unoriented line: its sign must not change leader sites
            // or candidate tie-breaking.
            let flat = if flat
                .iter()
                .find(|x| x.abs() > 1e-12)
                .is_some_and(|x| *x < 0.)
            {
                flat.map(|x| -x)
            } else {
                flat
            };
            let mut ax = [0.; 3];
            ax[k] = 1.;
            let tangent = [
                ax[1] * flat[2] - ax[2] * flat[1],
                ax[2] * flat[0] - ax[0] * flat[2],
                ax[0] * flat[1] - ax[1] * flat[0],
            ]
            .map(|x| x / n);
            let mut terms = vec![format!("⌀{}", fmt(major))];
            let mut measurements = vec![
                Measurement {
                    feature: f.id.clone(),
                    parameter: "bore.diameter".into(),
                    value: major,
                },
                Measurement {
                    feature: f.id.clone(),
                    parameter: "profile_across_flats.length".into(),
                    value: across,
                },
            ];
            if through {
                terms.push("THRU".into());
            } else {
                let depth = size("depth")?;
                terms.extend(["↧".into(), fmt(depth)]);
                measurements.push(Measurement {
                    feature: f.id.clone(),
                    parameter: "bore.depth".into(),
                    value: depth,
                });
            }
            terms.extend(["DOUBLE-D".into(), fmt(across), "A/F".into()]);
            let sites = [-1., 1.].map(|sign| {
                (
                    [0, 1, 2].map(|i| centre[i] + sign * 0.5 * major * tangent[i]),
                    0.,
                )
            });
            Ok(Want{ units: cx.spec.units,id:want_id(&f.id,"bore.diameter"),views:vec![end_on(k)],value:major,terms,measurements,
                turning:None,basis:"a double-D bore's major diameter and across flats in one callout, led to its circular rim in the view along its axis".into(),
                shape:Shape::Callout{sites:sites.to_vec(), arcs: Vec::new()}})
        };
        match read() {
            Ok(w) => out.push(w),
            Err(why) => problems.push(Problem {
                id: format!("p:feature_not_dimensioned:{}", f.id),
                code: "feature_not_dimensioned".into(),
                severity: "warning".into(),
                message: format!("Double-D bore {} is not dimensioned: {why}.", f.id),
                subjects: vec![f.id.clone()],
                ..Default::default()
            }),
        }
    }
    (out, problems)
}
