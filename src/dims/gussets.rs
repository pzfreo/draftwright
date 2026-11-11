//! Gusset groups use lint's checked reader. The triangle's two legs are seen end-on;
//! thickness, pitch/spacing and the box-to-centre location are seen along a leg.

use super::{AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, want_id};
use crate::lint::gussets::{Arrangement, groups};

pub(super) const PLANNER: Planner = Planner {
    kinds: &["gusset_rib", "gusset_rib_pattern"],
    wants,
};

fn linear(
    owner: &str,
    parameter: &str,
    a: P3,
    b: P3,
    axis: usize,
    view: &'static str,
    count: usize,
    units: crate::units::Units,
) -> Want {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let value = (b[axis] - a[axis]).abs();
    Want {
        units: units,
        id: want_id(owner, parameter),
        views: vec![view],
        value,
        terms: if count > 1 {
            vec![format!("{count}×"), fmt(value)]
        } else {
            vec![fmt(value)]
        },
        measurements: vec![Measurement {
            feature: owner.into(),
            parameter: parameter.into(),
            value,
        }],
        basis: format!(
            "a gusset group's {parameter}, stated once in the view that shows the span true"
        ),
        shape: Shape::Linear {
            a,
            b,
            axis,
            a_edge: false,
            b_edge: false,
            order: 0,
        },
        turning: None,
    }
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for group in groups(cx.analysis) {
        let (ribs, arrangement) = match group.read {
            Ok(read) => read,
            Err(_) => {
                // Keep the existing port-gap reports for records lint cannot read.
                for f in std::iter::once(group.owner).chain(group.members) {
                    let id = format!("p:dimensions_not_ported:{}", f.id);
                    if problems.iter().any(|p: &Problem| p.id == id) {
                        continue;
                    }
                    problems.push(Problem {
                        id,
                        code: "dimensions_not_ported".into(),
                        severity: "info".into(),
                        message: format!(
                            "{} is not dimensioned: no dimensions for {} are ported yet.",
                            crate::wording::capital(&crate::wording::feature(f)),
                            f.kind.replace('_', " ")
                        ),
                        subjects: vec![f.id.clone()],
                        ..Default::default()
                    });
                }
                continue;
            }
        };
        let start = out.len();
        let r = &ribs[0];
        let id = &group.owner.id;
        let centre = |i: usize| (ribs[i].bounds[0] + ribs[i].bounds[1]) / 2.0;
        let mut corner = [0.0; 3];
        corner[r.axis] = r.bounds[0];
        for (k, at) in r.supports {
            corner[k] = at;
        }
        let transverse = end_on(r.supports[0].0);
        let mut a = corner;
        let mut b = corner;
        b[r.axis] = r.bounds[1];
        out.push(linear(
            id,
            "gusset_thickness.length",
            a,
            b,
            r.axis,
            transverse,
            ribs.len(),
            cx.spec.units,
        ));
        for (i, (k, _)) in r.supports.iter().enumerate() {
            b = corner;
            b[*k] += r.directions[i] * r.legs[i];
            out.push(linear(
                id,
                &format!("gusset_leg.length.{}", AXES[*k]),
                corner,
                b,
                *k,
                end_on(r.axis),
                ribs.len(),
                cx.spec.units,
            ));
        }
        a[r.axis] = centre(0);
        b = a;
        let target = match arrangement {
            Arrangement::Single => centre(0),
            Arrangement::Linear { .. } | Arrangement::Mirror { .. } => {
                b[r.axis] = centre(1);
                let parameter = if matches!(arrangement, Arrangement::Linear { .. }) {
                    "gusset_pitch.length"
                } else {
                    "gusset_spacing.length"
                };
                out.push(linear(
                    id,
                    parameter,
                    a,
                    b,
                    r.axis,
                    transverse,
                    1,
                    cx.spec.units,
                ));
                match arrangement {
                    Arrangement::Mirror { plane, .. } => plane,
                    _ => centre(0),
                }
            }
        };
        // Locate the group from the box at its outward silhouette, leaving the
        // short thickness and centre-spacing spans beside the support corner.
        for (i, (k, _)) in r.supports.iter().enumerate() {
            a[*k] = cx.analysis.envelope[usize::from(r.directions[i] > 0.0)][*k];
        }
        b = a;
        a[r.axis] = cx.analysis.envelope[0][r.axis];
        b[r.axis] = target;
        out.push(linear(
            id,
            "gusset_location.length",
            a,
            b,
            r.axis,
            transverse,
            1,
            cx.spec.units,
        ));
        // One group statement also owns each member's equal section, as counted
        // hole and slot statements do. Completeness can then follow the drawn ink.
        for w in &mut out[start..] {
            let m = &w.measurements[0];
            if m.parameter == "gusset_thickness.length" || m.parameter.starts_with("gusset_leg.") {
                let parameter = m.parameter.clone();
                w.measurements
                    .extend(
                        group
                            .members
                            .iter()
                            .filter(|m| m.id != *id)
                            .map(|m| Measurement {
                                feature: m.id.clone(),
                                parameter: parameter.clone(),
                                value: w.value,
                            }),
                    );
            }
        }
    }
    (out, problems)
}

/// A group's transverse view is author intent; the triangle remains in its true view.
pub(super) fn apply(spec: &crate::model::Spec, wants: &mut [Want]) {
    for w in wants {
        if let Some(m) = w.measurements.first()
            && m.parameter.starts_with("gusset_")
            && !m.parameter.starts_with("gusset_leg.")
            && let Some(view) = spec
                .features
                .get(&m.feature)
                .and_then(|f| f.gusset_view.as_deref())
            && let Some(view) = ["front", "side", "plan"].into_iter().find(|v| *v == view)
        {
            w.views = vec![view];
            w.basis += "; transverse view selected by features/<id>/gusset_view";
        }
    }
}

/// An existing statement of the same projected leg span can carry its group claim.
/// Equal numbers at other stations, authored placements and tolerance bands stay distinct.
pub(super) fn share(
    kept: &mut Vec<(Want, bool, Option<&'static str>, Vec<&'static str>)>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<super::Omitted>,
    units: crate::units::Units,
) {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let mut i = 0;
    while i < kept.len() {
        let (w, default, view, _) = &kept[i];
        let leg = w
            .measurements
            .iter()
            .all(|m| m.parameter.starts_with("gusset_leg.length."));
        if !leg || w.measurements.is_empty() || !default || !given[i].is_empty() {
            i += 1;
            continue;
        }
        let Shape::Linear { a, b, axis, .. } = w.shape else {
            i += 1;
            continue;
        };
        let view = view.unwrap_or(w.views[0]);
        let normal = (0..3).find(|&k| end_on(k) == view);
        let equal = |p: P3, q: P3| (0..3).all(|k| Some(k) == normal || (p[k] - q[k]).abs() <= 1e-6);
        let owner = kept.iter().enumerate().find_map(|(j, (other, _, v, _))| {
            if i == j
                || v.unwrap_or(other.views[0]) != view
                || other.terms != [fmt(w.value)]
                || !given[j].is_empty()
                || other
                    .measurements
                    .iter()
                    .any(|m| m.parameter.starts_with("gusset_"))
            {
                return None;
            }
            match other.shape {
                Shape::Linear {
                    a: p,
                    b: q,
                    axis: k,
                    ..
                } if k == axis
                    && ((equal(a, p) && equal(b, q)) || (equal(a, q) && equal(b, p))) =>
                {
                    Some(j)
                }
                _ => None,
            }
        });
        if let Some(j) = owner {
            let by = kept[j].0.id.clone();
            let claims = kept[i].0.measurements.clone();
            kept[j].0.measurements.extend(claims);
            omitted.push(super::Omitted {
                id: kept[i].0.id.clone(),
                label: kept[i].0.terms.join(" "),
                basis: format!("conveyed by {by}: the same gusset leg span is already stated"),
                state: crate::items::State::Covered { by },
            });
            kept.remove(i);
            given.remove(i);
        } else {
            i += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_same_leg_span_is_shared_and_an_override_keeps_it() {
        for (shift, authored, count) in [(0., false, 1), (10., false, 2), (0., true, 2)] {
            let leg = linear(
                "rib",
                "gusset_leg.length.z",
                [0., 0., 8.],
                [0., 0., 38.],
                2,
                "side",
                2,
                crate::units::Units::Mm,
            );
            let other = linear(
                "plate",
                "height.length",
                [0., 0., 8. + shift],
                [0., 0., 38. + shift],
                2,
                "side",
                1,
                crate::units::Units::Mm,
            );
            let mut kept = vec![
                (other, true, Some("side"), vec![]),
                (leg, !authored, Some("side"), vec![]),
            ];
            let mut given = vec![vec![], vec![]];
            let mut omitted = vec![];
            share(&mut kept, &mut given, &mut omitted, crate::units::Units::Mm);
            assert_eq!(kept.len(), count);
            assert_eq!(given.len(), count);
            if count == 1 {
                assert_eq!(kept[0].0.measurements.len(), 2);
                assert_eq!(omitted.len(), 1);
            }
        }
    }
}
