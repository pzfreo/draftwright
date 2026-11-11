//! Turned grooves (quiddity's `Groove`, draftwright's `GrooveFeature`): one callout each,
//! `10 WIDE × ø122`, its axial width and floor diameter (`render_grooves` and `_groove_label` in
//! `annotations/from_model.py`).
//!
//! - **View:** the groove's width is axial, so it reads where the turning axis lies in the view,
//!   the groove a notch in the silhouette: [`profile_views`] its axis (draftwright's `_EDGE_ON`:
//!   x and z in the front, y in the side). draftwright skips the groove when that view is not
//!   shown; the port falls back to the next view that shows the profile.
//! - **Grouping:** grooves of one turned profile (one axis line, one recorded profile) whose
//!   callouts print alike are called out once and counted, `2× 1.2 WIDE × ø13.4`, with a leader
//!   site at each (beyond draftwright, which calls each out on its own: #220). Each groove's
//!   axial position is the step chain's, not the callout's.
//! - **Leader site:** the floor's silhouette, the record's `at` (on the axis) moved out by the
//!   floor radius across the axis.
//!
//! A groove whose record lacks its width, floor diameter, position or a principal axis is
//! reported (`feature_not_dimensioned`), never dropped; so is one on a part of several turned
//! profiles whose record names none of them (ADR3-12: it has no one owner). A boss that is a groove's floor is left
//! to the groove ([`super::bosses`]).
//!
//! Annular face grooves (quiddity's `AnnularGroove`, #233): a blind ring between two coaxial
//! walls, sunk into a face. Its outer wall reads as a blind hole and its inner wall as a boss,
//! and where both are recognised their callouts (`⌀D ↧ depth`, `øD`) and the hole's location
//! state the whole groove, so it adds nothing, unless the spec omits one of those sizes
//! ([`super::restate`]). Otherwise it is called out in the view down its
//! axis, `⌀OD × ⌀ID ↧ depth`, with a leader to its outer rim, and its centre is located from the
//! part's box on that view's two axes. A groove whose axis is along no axis of the part is
//! reported (`feature_not_dimensioned`).

use serde_json::Value;

use super::envelope::profile_views;
use super::{
    AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, point, want_id,
};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, principal};

pub(super) const PLANNER: Planner = Planner {
    kinds: &["groove", "annular_groove", "face_groove"],
    wants,
};

/// The warning for a recognised feature the planner cannot state (as [`super::bosses`] gives).
pub(super) fn not_dimensioned(f: &Feature, why: &str) -> Problem {
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

/// A record's size *name*: a positive number, or why not.
pub(super) fn size(f: &Feature, name: &str) -> Result<f64, String> {
    f.parameters
        .get(name)
        .and_then(Value::as_f64)
        .filter(|x| *x > 0.0)
        .ok_or_else(|| format!("its record gives no positive {name}"))
}

/// The groove's callout, or why it cannot be stated.
fn want(cx: &Context, f: &Feature) -> Result<Want, String> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let p = &f.parameters;
    let width = size(f, "width")?;
    let diameter = size(f, "diameter")?;
    let at = cx
        .frame
        .point(point(p.get("at")).ok_or("its record gives no at")?);
    let axis = p
        .get("axis")
        .and_then(|a| cx.frame.letter(a))
        .ok_or("its record gives no axis")?;
    let k = principal(axis)
        .ok_or("its axis is not along x, y or z, so no standard view shows its profile")?;
    let r = 0.5 * diameter;
    let sites: Vec<(P3, f64)> = (0..3)
        .filter(|&j| j != k)
        .flat_map(|j| {
            [-r, r].map(|d| {
                let mut s = at;
                s[j] += d;
                (s, 0.0)
            })
        })
        .collect();
    let views = profile_views(k);
    Ok(Want {
        units: cx.spec.units,
        id: want_id(&f.id, "groove.length"),
        views,
        value: width,
        terms: vec![
            fmt(width),
            "WIDE".into(),
            "×".into(),
            format!("ø{}", fmt(diameter)),
        ],
        measurements: vec![
            Measurement {
                feature: f.id.clone(),
                parameter: "groove.length".into(),
                value: width,
            },
            Measurement {
                feature: f.id.clone(),
                parameter: "groove.diameter".into(),
                value: diameter,
            },
        ],
        turning: None,
        basis: format!(
            "a groove's axial width and floor diameter are called out together, in a view that shows the profile about {}",
            AXES[k]
        ),
        shape: Shape::Callout {
            sites,
            arcs: Vec::new(),
        },
    })
}

/// ADR3-12: on a part of several turned profiles (the turned steps' recorded profiles), a groove
/// whose record names none of them cannot name its one owner, so it is refused rather than
/// called out on whichever body happens to lie under it.
fn owner(cx: &Context, f: &Feature) -> Result<(), String> {
    let mut profiles: Vec<&Value> = cx
        .analysis
        .features
        .iter()
        .filter(|s| s.kind == "turned_step")
        .filter_map(|s| s.parameters.get("profile"))
        .collect();
    profiles.sort_by_key(|p| p.to_string());
    profiles.dedup();
    if profiles.len() < 2
        || f.parameters
            .get("profile")
            .is_some_and(|p| profiles.contains(&p))
    {
        return Ok(());
    }
    Err(format!(
        "the part has {} turned profiles and its record names none of them, so it has no one owner",
        profiles.len()
    ))
}

/// A turned profile: its axis, a point on its axis line and its recorded profile.
type Line<'a> = (usize, P3, Option<&'a Value>);

/// The turned profile groove *f* lies on, when its record gives its position and axis.
fn line<'a>(cx: &Context, f: &'a Feature) -> Option<Line<'a>> {
    let p = &f.parameters;
    let at = cx.frame.point(point(p.get("at"))?);
    let k = principal(cx.frame.letter(p.get("axis")?)?)?;
    Some((k, at, p.get("profile")))
}

/// An annular groove's callout and centre locations, or why they cannot be stated.
pub(super) fn annular(cx: &Context, f: &Feature) -> Result<Vec<Want>, String> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let p = &f.parameters;
    let face = f.kind == "face_groove";
    let outer = if face {
        size(f, "outer_diameter")?
    } else {
        2.0 * size(f, "outer_radius")?
    };
    let inner = if face {
        size(f, "inner_diameter")?
    } else {
        2.0 * size(f, "inner_radius")?
    };
    let depth = size(f, "depth")?;
    let axis = dir_to_part(
        &cx.frame.0,
        point(p.get("axis")).ok_or("its record gives no axis")?,
    );
    let k = principal(axis)
        .ok_or("its axis is not along x, y or z, so no standard view shows it end-on")?;
    let floor = cx.frame.point(
        point(p.get(if face { "axis_point" } else { "floor_center" }))
            .ok_or("its record gives no floor centre")?,
    );
    let mut mouth = floor;
    if !face {
        mouth[k] += axis[k].signum() * depth;
    }
    let view = end_on(k);
    let m = |parameter: &str, value: f64| Measurement {
        feature: f.id.clone(),
        parameter: parameter.into(),
        value,
    };
    let mut out = vec![Want { units: cx.spec.units,
id: want_id(&f.id, "annular_groove_outer.diameter"),
        views: vec![view],
        value: outer,
        terms: vec![
            format!("⌀{}", fmt(outer)),
            "×".into(),
            format!("⌀{}", fmt(inner)),
            "↧".into(),
            fmt(depth),
        ],
        measurements: vec![
            m("annular_groove_outer.diameter", outer),
            m("annular_groove_inner.diameter", inner),
            m("annular_groove_depth.length", depth),
        ],
        turning: None,
        basis: "an annular groove's outer and inner diameters and depth are called out together, in the view down its axis".into(),
        shape: Shape::Callout {
            sites: vec![(mouth, 0.5 * outer)],
            arcs: Vec::new(),
        },
    }];
    if face {
        for (name, word) in [("outer_wall_angle", "OUTER"), ("inner_wall_angle", "INNER")] {
            let angle = size(f, name)?;
            // quiddity uses 90° for a cylindrical wall; only sloping walls need angles.
            if (angle - 90.0).abs() > 1e-3 {
                out[0]
                    .terms
                    .extend([word.into(), format!("{}°", crate::dims::fmt(angle))]);
                out[0]
                    .measurements
                    .push(m(&format!("face_groove_{name}.angle"), angle));
            }
        }
    }
    // A concentric face recess shares the turned body's established axis; locating it
    // from the box would repeat the radius as two spurious centre locations.
    let concentric = face
        && crate::bridge::turned_body(cx.analysis).is_some_and(|(a, on, _)| {
            a == k && (0..3).all(|j| j == k || (mouth[j] - on[j]).abs() <= 1e-3)
        });
    let lo = cx.analysis.envelope[0];
    for j in (0..3).filter(|&j| j != k) {
        if concentric && !cx.spec.origin.contains_key(AXES[j]) {
            continue;
        }
        let value = mouth[j] - lo[j];
        if value.abs() < 1e-6 {
            continue;
        }
        let parameter = format!("location.length.{}", AXES[j]);
        let mut a = mouth;
        a[j] = lo[j];
        out.push(Want { units: cx.spec.units,
            id: want_id(&f.id, &parameter),
            views: vec![view],
            value,
            terms: vec![fmt(value)],
            measurements: vec![m(&parameter, value)],
            turning: None,
            basis: format!(
                "an annular groove's centre located in {} from the part's {} face, in the view down its axis",
                AXES[j],
                ["left", "front", "bottom"][j]
            ),
            shape: Shape::Linear {
                a,
                b: mouth,
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
    let mut out: Vec<(Want, Option<Line>)> = Vec::new();
    let mut problems = Vec::new();
    for f in cx.analysis.features.iter().filter(|f| {
        (f.kind == "annular_groove"
            && crate::lint::grooves::annular_owners(cx.analysis, f).is_none())
            || f.kind == "face_groove"
    }) {
        match annular(cx, f) {
            Ok(ws) => {
                let on = if f.kind == "face_groove" {
                    point(f.parameters.get("axis"))
                        .and_then(|d| principal(dir_to_part(&cx.frame.0, d)))
                        .zip(point(f.parameters.get("axis_point")).map(|p| cx.frame.point(p)))
                        .map(|(k, p)| (k, p, None))
                } else {
                    None
                };
                for w in ws {
                    // Opposed face recesses are separate occurrences, but equal coaxial
                    // recesses share one counted size statement, just like turned grooves.
                    let same =
                        if f.kind == "face_groove" && matches!(w.shape, Shape::Callout { .. }) {
                            out.iter().position(|(o, l)| {
                                on.zip(*l).is_some_and(|((k, p, _), (j, q, _))| {
                                    k == j && (0..3).all(|a| a == k || (p[a] - q[a]).abs() <= 1e-3)
                                }) && o.terms.ends_with(&w.terms)
                                    && o.measurements
                                        .iter()
                                        .any(|m| m.feature.starts_with("f:face_groove:"))
                            })
                        } else {
                            None
                        };
                    if let Some(i) = same {
                        let o = &mut out[i].0;
                        o.measurements.extend(w.measurements);
                        if let (Shape::Callout { sites, .. }, Shape::Callout { sites: more, .. }) =
                            (&mut o.shape, w.shape)
                        {
                            sites.extend(more);
                        }
                        let n = o
                            .measurements
                            .iter()
                            .map(|m| &m.feature)
                            .collect::<std::collections::BTreeSet<_>>()
                            .len();
                        if n == 2 {
                            o.terms.insert(0, String::new());
                        }
                        o.terms[0] = format!("{n}×");
                        o.basis = "equal annular face recesses share one counted callout, retaining each rim diameter, depth and sloping wall angle".into();
                    } else {
                        out.push((w, on));
                    }
                }
            }
            Err(why) => problems.push(not_dimensioned(f, &why)),
        }
    }
    for f in cx.analysis.features.iter().filter(|f| f.kind == "groove") {
        let w = match owner(cx, f).and_then(|()| want(cx, f)) {
            Ok(w) => w,
            Err(why) => {
                problems.push(not_dimensioned(f, &why));
                continue;
            }
        };
        // An earlier groove of the same profile, printing alike, counts this one.
        let on = line(cx, f);
        let same = on.and_then(|(k, at, profile)| {
            out.iter().position(|(o, l)| {
                o.terms.ends_with(&w.terms)
                    && l.is_some_and(|(k2, at2, profile2)| {
                        k2 == k
                            && profile2 == profile
                            && (0..3).all(|j| j == k || (at[j] - at2[j]).abs() <= 1e-3)
                    })
            })
        });
        let (Some(i), Some((k, ..))) = (same, on) else {
            out.push((w, on));
            continue;
        };
        let o = &mut out[i].0;
        o.measurements.extend(w.measurements);
        if let (Shape::Callout { sites, .. }, Shape::Callout { sites: more, .. }) =
            (&mut o.shape, w.shape)
        {
            sites.extend(more);
        }
        let n = o.measurements.len() / 2;
        if n == 2 {
            o.terms.insert(0, String::new());
        }
        o.terms[0] = format!("{n}×");
        o.basis = format!(
            "{n} grooves of one turned profile have one width and floor diameter, so one callout counts them, in a view that shows the profile about {}",
            AXES[k]
        );
    }
    (out.into_iter().map(|(w, _)| w).collect(), problems)
}
