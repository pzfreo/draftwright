//! A V is specified by included angle, theoretical sharp depth, and mouth position.
//!
//! quiddity's V channels (`VChannel`, #233) are the same requirement as a feature. One whose
//! flanks a profile V-notch here already names is stated by that notch, unless the spec omits
//! the notch's angle or depth ([`super::restate`]). Otherwise it is
//! called out in the view along its run, `A° V × D DEEP` (the included angle and the depth to
//! the sharp or virtual trough, which fix its width), with a leader to a flank, and its trough
//! is located from the part's box on that view's axes, except along a principal opening, where
//! the mouth's face and the depth fix it. A rounded trough's radius is its blend's. A channel
//! whose run is along no axis of the part is reported (`feature_not_dimensioned`).
use super::grooves::{not_dimensioned, size};
use super::{
    AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, point, want_id,
};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, principal};

/// Profile requirements belong to the part envelope; the support stamp distinguishes
/// physical corners without pretending that the provider published a notch feature.
pub(super) fn id(feature: &str, parameter: &str) -> String {
    let role = parameter.strip_prefix("location.length.").map_or_else(
        || parameter.to_owned(),
        |axis| format!("notch_location_{axis}.length"),
    );
    let support = feature
        .strip_prefix("notch:")
        // Invariant: Notch requirements are emitted only after validating their source profile.
        .expect("source-proven notch")
        .replace(':', "_");
    want_id("envelope", &format!("{role}.{support}"))
}

pub(super) const PLANNER: Planner = Planner {
    kinds: &["v_channel", "rectangular_through_notch"],
    wants,
};

/// Width and depth of a published through-notch, with its mouth located on both
/// in-plane axes. Corner radii retain the existing blend/fillet callouts.
fn rectangular(cx: &Context, f: &Feature) -> Result<Vec<Want>, String> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let n = crate::lint::rectangular_notches::read(cx.analysis, f)?;
    let view = end_on(n.axis);
    let mut out = Vec::new();
    let mut push = |parameter: String, a: P3, b: P3, axis: usize, a_edge, basis: &str| {
        let value = (b[axis] - a[axis]).abs();
        if value < 1e-6 {
            return;
        }
        let order = u8::from(parameter.starts_with("location."));
        out.push(Want {
            units: cx.spec.units,
            id: want_id(&f.id, &parameter),
            views: vec![view],
            value,
            terms: vec![fmt(value)],
            measurements: vec![Measurement {
                feature: f.id.clone(),
                parameter,
                value,
            }],
            turning: None,
            basis: basis.into(),
            shape: Shape::Linear {
                a,
                b,
                axis,
                a_edge,
                b_edge: false,
                order,
            },
        });
    };
    // Witness the straight walls, clear of the rounded bottom corners.
    let mut a = n.mouth;
    let mut b = a;
    a[n.wide] -= 0.5 * n.width;
    b[n.wide] += 0.5 * n.width;
    push(
        "notch_width.length".into(),
        a,
        b,
        n.wide,
        false,
        "rectangular through-notch width between its straight walls, in the view through the plate",
    );
    // Use the middle of the bottom wall and the mouth's support plane.
    push(
        "notch_depth.length".into(),
        n.at,
        n.mouth,
        n.deep,
        false,
        "rectangular through-notch depth from its mouth plane to its bottom wall",
    );
    for k in [n.wide, n.deep]
        .into_iter()
        .filter(|&k| n.needs_location(cx.analysis, k))
    {
        let b = n.locator();
        let mut a = b;
        a[k] = cx.analysis.envelope[0][k];
        push(
            format!("location.length.{}", AXES[k]),
            a,
            b,
            k,
            true,
            "rectangular through-notch mouth corner located from an envelope edge in its end-on view",
        );
    }
    // A common opening plane needs one ordinate, with both physical features
    // retained as endpoints in the location graph.
    let common: Vec<_> = cx
        .analysis
        .features
        .iter()
        .filter_map(|g| {
            if g.kind != "rectangular_through_notch" {
                return None;
            }
            let m = crate::lint::rectangular_notches::read(cx.analysis, g).ok()?;
            (m.axis == n.axis
                && m.deep == n.deep
                && (m.mouth[n.deep] - n.mouth[n.deep]).abs() < 1e-6
                && (m.mouth[n.deep] - m.at[n.deep]).signum()
                    == (n.mouth[n.deep] - n.at[n.deep]).signum())
            .then_some(g)
        })
        .collect();
    let role = format!("location.length.{}", AXES[n.deep]);
    if common.first().is_some_and(|g| g.id != f.id) {
        out.retain(|w| !w.measurements.iter().any(|m| m.parameter == role));
    } else if let Some(w) = out
        .iter_mut()
        .find(|w| w.measurements.iter().any(|m| m.parameter == role))
    {
        for g in common.into_iter().filter(|g| g.id != f.id) {
            w.measurements.push(Measurement {
                feature: g.id.clone(),
                parameter: role.clone(),
                value: w.value,
            });
        }
    }
    Ok(out)
}

/// Equivalent physical witnesses for a shared opening ordinate or depth.
/// Moving along the same support plane does not change the measured span.
pub(super) fn references(w: &Want, analysis: &crate::model::Analysis) -> Vec<Shape> {
    let Shape::Linear {
        axis,
        a_edge,
        b_edge,
        order,
        ..
    } = w.shape
    else {
        return Vec::new();
    };
    w.measurements
        .iter()
        .filter_map(|m| {
            let f = analysis
                .features
                .iter()
                .find(|f| f.id == m.feature && f.kind == "rectangular_through_notch")?;
            let n = crate::lint::rectangular_notches::read(analysis, f).ok()?;
            (axis == n.deep).then_some((n, m.parameter.starts_with("location.")))
        })
        .flat_map(|(n, location)| {
            let mut out = Vec::new();
            for offset in if location {
                vec![-0.5 * n.width, 0.5 * n.width]
            } else {
                vec![0.]
            } {
                let mut b = n.mouth;
                b[n.wide] += offset;
                let mut a = if location { b } else { n.at };
                if location {
                    a[axis] = analysis.envelope[0][axis];
                }
                out.push(Shape::Linear {
                    a,
                    b,
                    axis,
                    a_edge,
                    b_edge,
                    order,
                });
            }
            out
        })
        .take(8)
        .collect()
}

/// A V channel's callout and trough locations, or why they cannot be stated.
pub(super) fn channel(cx: &Context, f: &Feature) -> Result<Vec<Want>, String> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let p = &f.parameters;
    let angle = size(f, "included_angle_degrees")?;
    let depth = size(f, "depth")?;
    let width = size(f, "width")?;
    let dir = |name: &str| -> Result<P3, String> {
        let d = point(p.get(name)).ok_or_else(|| format!("its record gives no {name}"))?;
        Ok(dir_to_part(&cx.frame.0, d))
    };
    let (axis, opening) = (dir("axis")?, dir("opening")?);
    let k = principal(axis)
        .ok_or("its run is not along x, y or z, so no standard view shows its section")?;
    let at = cx
        .frame
        .point(point(p.get("at")).ok_or("its record gives no at")?);
    let across = super::angular::cross(axis, opening);
    // A point three quarters up a flank, clear of a rounded trough.
    let flank =
        std::array::from_fn(|j| at[j] + 0.75 * (opening[j] * depth + across[j] * 0.5 * width));
    let view = end_on(k);
    let m = |parameter: &str, value: f64| Measurement {
        feature: f.id.clone(),
        parameter: parameter.into(),
        value,
    };
    let mut out = vec![Want { units: cx.spec.units,
id: want_id(&f.id, "v_channel.angle"),
        views: vec![view],
        value: angle,
        terms: vec![
            format!("{}°", crate::dims::fmt(angle)),
            "V".into(),
            "×".into(),
            fmt(depth),
            "DEEP".into(),
        ],
        measurements: vec![
            m("v_channel.angle", angle),
            m("v_channel_depth.length", depth),
        ],
        turning: None,
        basis: "a V channel's included angle and depth to its theoretical sharp trough, which fix its width, are called out together, in the view along its run".into(),
        shape: Shape::Callout {
            sites: vec![(flank, 0.0)],
            arcs: Vec::new(),
        },
    }];
    let lo = cx.analysis.envelope[0];
    let fixed = principal(opening);
    for j in (0..3).filter(|&j| j != k && Some(j) != fixed) {
        let value = at[j] - lo[j];
        if value.abs() < 1e-6 {
            continue;
        }
        let parameter = format!("location.length.{}", AXES[j]);
        let mut a = at;
        a[j] = lo[j];
        out.push(Want { units: cx.spec.units,
            id: want_id(&f.id, &parameter),
            views: vec![view],
            value,
            terms: vec![fmt(value)],
            measurements: vec![m(&parameter, value)],
            turning: None,
            basis: format!(
                "a V channel's trough located in {} from the part's {} face, in the view along its run",
                AXES[j],
                ["left", "front", "bottom"][j]
            ),
            shape: Shape::Linear {
                a,
                b: at,
                axis: j,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
        });
    }
    Ok(out)
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for f in cx
        .analysis
        .features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
    {
        match rectangular(cx, f) {
            Ok(ws) => out.extend(ws),
            Err(why) => problems.push(not_dimensioned(f, &why)),
        }
    }
    let notches = crate::notches::requirements(cx.analysis);
    for f in cx.analysis.features.iter().filter(|f| {
        f.kind == "v_channel" && crate::lint::notch_evidence::channel_owner(&notches, f).is_none()
    }) {
        match channel(cx, f) {
            Ok(ws) => out.extend(ws),
            Err(why) => problems.push(not_dimensioned(f, &why)),
        }
    }
    // Keep the existing front corridor when it also carries GD&T. Splitting
    // these stations across views removes a front tier and can displace a
    // control frame into a datum's otherwise clear compact route.
    let front_locations = !notches.is_empty()
        && crate::pmi_lowering::lower(cx.analysis, &|_| true)
            .0
            .iter()
            .any(|d| {
                d.view == "front"
                    && matches!(
                        &d.glyph,
                        crate::pmi_lowering::Glyph::ControlFrame { .. }
                            | crate::pmi_lowering::Glyph::Datum { .. }
                    )
            });
    for n in notches {
        let view = end_on(n.axis);
        let value = n.angle.degrees();
        out.push(Want { units: cx.spec.units,
            id: id(&n.id,"notch.angle"), views:vec![view], value,
            terms:vec![format!("{}°",crate::dims::fmt(value))],
            measurements:vec![Measurement {feature:n.id.clone(),parameter:"notch.angle".into(),value}],
            basis:"a V-notch's included angle, in the normal view that shows it true; rounded roots extend to the theoretical sharp apex".into(),
            shape:Shape::Angular {angle:(&n.angle).into(), alternates: Vec::new()}, turning:None,
        });
        let k = n.depth_axis;
        let value = n.depth();
        out.push(Want { units: cx.spec.units,
            id:id(&n.id,"notch_depth.length"),views:vec![view],value,terms:vec![fmt(value)],
            measurements:vec![Measurement {feature:n.id.clone(),parameter:"notch_depth.length".into(),value}],
            basis:"V-notch depth from the mouth to the theoretical sharp apex; the included angle and depth define the mouth width".into(),
            shape:Shape::Linear {a:n.mouth,b:n.angle.vertex,axis:k,a_edge:false,b_edge:false,order:0},turning:None,
        });
        // Locate the mouth centre from the part's minimum datums; consolidation shares
        // coincident stations with existing dimensions and schemes can take ownership.
        for k in [n.width_axis, n.depth_axis] {
            let b = n.mouth;
            let mut a = b;
            a[k] = cx.analysis.envelope[0][k];
            let value = (b[k] - a[k]).abs();
            if value < 1e-6 {
                continue;
            }
            let parameter = format!("location.length.{}", AXES[k]);
            out.push(Want {
                units: cx.spec.units,
                id: id(&n.id, &parameter),
                // An X station is equally true in the aligned front projection.
                // Keep the plan's angular sector free of long datum witnesses; an
                // explicit scheme or placement can still choose the normal view.
                views: if n.axis == 2 && k == 0 {
                    if !front_locations
                        && n.mouth[k]
                            < 0.5 * (cx.analysis.envelope[0][k] + cx.analysis.envelope[1][k])
                    {
                        vec![view, "front"]
                    } else {
                        vec!["front", view]
                    }
                } else if n.axis == 2 && k == 1 {
                    vec!["side", view]
                } else {
                    vec![view]
                },
                value,
                terms: vec![fmt(value)],
                measurements: vec![Measurement {
                    feature: n.id.clone(),
                    parameter,
                    value,
                }],
                basis: "the V-notch mouth centre located from the part's minimum datum".into(),
                shape: Shape::Linear {
                    a,
                    b,
                    axis: k,
                    a_edge: true,
                    b_edge: false,
                    order: 1,
                },
                turning: None,
            });
        }
    }
    // A bevel is an edge treatment: its C/size-and-angle callout offsets the drawn
    // supports. Its provider corner is the midpoint of a run, not a design datum;
    // do not invent a station requirement there. Explicit schemes can still name it.
    (out, problems)
}

/// Only bounded profile angles yield to their source PMI, retaining its tolerance
/// and implicit degree notation. Other angular planning keeps its existing policy.
pub(super) fn share_pmi(
    analysis: &crate::model::Analysis,
    kept: &mut Vec<(Want, bool, Option<&'static str>, Vec<&'static str>)>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<super::Omitted>,
) {
    let mut gone = std::collections::BTreeSet::new();
    for n in crate::notches::requirements(analysis) {
        let sources = crate::lint::notch_evidence::pmi_angles(analysis, &n);
        let Some(p) = kept.iter().position(|(w, ..)| {
            matches!(w.shape, Shape::Angular { .. })
                && w.measurements.iter().any(|m| {
                    sources.contains(&m.feature)
                        && matches!(m.parameter.as_str(), "angular" | "angular.basic")
                })
        }) else {
            continue;
        };
        if kept[p].2 != Some(end_on(n.axis)) {
            continue;
        }
        // The live source statement proves the role even when the user has
        // omitted its automatic alias. Only a live, true-view PMI can own it.
        if !kept[p]
            .0
            .measurements
            .iter()
            .any(|m| m.feature == n.id && m.parameter == "notch.angle")
        {
            kept[p].0.measurements.push(Measurement {
                feature: n.id.clone(),
                parameter: "notch.angle".into(),
                value: n.angle.degrees(),
            });
            kept[p].0.basis +=
                "; also states the bounded V-notch angle on its source support faces";
        }
        let Some(i) = kept.iter().position(|(w, default, view, _)| {
            *default && *view == kept[p].2 && w.id == id(&n.id, "notch.angle")
        }) else {
            continue;
        };
        if !given[i].is_empty() {
            continue;
        }
        let owner = kept[p].0.id.clone();
        omitted.push(super::Omitted {
            id: kept[i].0.id.clone(), label: kept[i].0.terms.join(" "),
            basis: format!("conveyed by {owner}: the source PMI states this bounded notch angle on the same physical support faces in the true normal view"),
            state: crate::items::State::Covered { by: owner },
        });
        gone.insert(i);
    }
    let mut i = 0;
    kept.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
    let mut i = 0;
    given.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
}

/// Reuse an already printed first bevel leg instead of adding the same depth line.
pub(super) fn share_bevel_depths(
    analysis: &crate::model::Analysis,
    kept: &mut Vec<(Want, bool, Option<&'static str>, Vec<&'static str>)>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<super::Omitted>,
) {
    let mut gone = std::collections::BTreeSet::new();
    for n in crate::notches::requirements(analysis) {
        let alias = id(&n.id, "notch_depth.length");
        // A source PMI interval or another truthful owner has first claim.
        if kept.iter().any(|(w, ..)| {
            w.id != alias
                && w.measurements
                    .iter()
                    .any(|m| m.feature == n.id && m.parameter == "notch_depth.length")
        }) {
            continue;
        }
        let sources = crate::lint::notch_evidence::bevel_depths(analysis, &n);
        let Some(p) = kept.iter().position(|(w, _, view, _)| {
            *view == Some(end_on(n.axis))
                && w.measurements.iter().any(|m| {
                    sources.contains(&m.feature)
                        && matches!(
                            m.parameter.as_str(),
                            "chamfer.length" | "oriented_chamfer.length"
                        )
                })
        }) else {
            continue;
        };
        kept[p].0.measurements.push(Measurement {
            feature: n.id.clone(),
            parameter: "notch_depth.length".into(),
            value: n.depth(),
        });
        kept[p].0.basis += "; its first support leg also states the bounded V-notch depth";
        let Some(i) = kept
            .iter()
            .position(|(w, default, view, _)| *default && *view == kept[p].2 && w.id == alias)
        else {
            continue;
        };
        if !given[i].is_empty() {
            continue;
        }
        let owner = kept[p].0.id.clone();
        omitted.push(super::Omitted {
            id: alias, label: kept[i].0.terms.join(" "),
            basis: format!("conveyed by {owner}: the existing bevel first leg spans this same physical notch depth"),
            state: crate::items::State::Covered { by: owner },
        });
        gone.insert(i);
    }
    let mut i = 0;
    kept.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
    let mut i = 0;
    given.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
}
