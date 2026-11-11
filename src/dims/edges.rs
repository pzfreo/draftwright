//! Edge treatments: a fillet's radius (`R3`) and a chamfer's size (`C1` for equal legs at 45°,
//! otherwise `4.5 × 42.1°`), as draftwright calls them out (`annotations/_edge_callouts.py`).
//!
//! - **Grouping:** fillets that print the same radius share one `n× R` callout; chamfers share
//!   one when their legs and angle are equal, not merely their printed first leg (ISSUE-1254-a).
//!   The callout states every member's measurement, and points at the members of its most
//!   populous axis and view (ties to the first in axis, then view, order), as draftwright's
//!   `radius_jobs` and `chamfer_jobs` do; members along other axes are counted on it too.
//! - **View:** a prismatic treatment reads where its edge is seen end-on (the rounded edge as an
//!   arc, the bevel edge-on), [`end_on`] its axis; a turned one (quiddity's `turned`, a toroidal
//!   round or conical bevel about the shaft) reads in the shaft's profile, [`profile_views`]. A
//!   record is never guessed to be turned (ISSUE-1281-a).
//! - **Leader site:** the record's `at`. A turned treatment is rotated onto the profile its view
//!   shows about the coaxial turned band whose span holds its station (or, failing one, a part
//!   turned whole's axis), as draftwright's `_turned_profile_site` does about the nearest
//!   external shaft (ISSUE-1276-a); a parallel shaft elsewhere does not attract it. A fillet
//!   callout whose site misses every arc its view sees end-on leads instead to the middle half
//!   of its members' trimmed arcs there ([`route_profile`], E4), never to a straight edge.
//! - **Oriented chamfers** state the true support-plane leg and section angle in a leader
//!   callout. No projected angle is measured. Equal treatments share the existing C callout;
//!   a principal-edge member retains presentation ownership when both families occur.
//!
//! A record whose sizes or direction cannot be read is reported (`feature_not_dimensioned`),
//! never dropped.

use std::collections::BTreeMap;

use serde_json::Value;

use super::envelope::profile_views;
use super::turned::Turned;
use super::{AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, key};
use crate::bridge::Feature;
use crate::bridge::holes::{self, point, principal};
use crate::lint::turned::Band;

pub(super) const PLANNER: Planner = Planner {
    kinds: &[
        "chamfer",
        "fillet",
        "oriented_chamfer",
        "cylindrical_profile",
    ],
    wants,
};

/// Whether a chamfer of these sizes is called out `C<leg>`: draftwright's `_fmt_chamfer`.
fn c_form(leg1: f64, leg2: f64, angle: f64) -> bool {
    (leg1 - leg2).abs() < 0.05 && (angle - 45.0).abs() < 0.5
}

/// A recognised fillet or chamfer the callout grammar can state.
struct Edge<'a> {
    feature: &'a Feature,
    /// `fillet.radius` or `chamfer.length`.
    parameter: &'static str,
    /// The world axis of its edge, or of its shaft when it is turned.
    axis: usize,
    turned: bool,
    oriented: bool,
    /// Its record's `at`, in the part's frame.
    at: P3,
    /// The radius, or the first leg.
    value: f64,
    /// The terms after the count.
    terms: Vec<String>,
    /// What makes two of them one requirement: the printed radius, or the legs and angle.
    same: Vec<i64>,
    measurements: Vec<Measurement>,
}

impl Edge<'_> {
    /// The views that can carry it, preferred first.
    fn views(&self) -> Vec<&'static str> {
        if self.turned {
            profile_views(self.axis)
        } else {
            vec![end_on(self.axis)]
        }
    }
}

/// A record's size *name*: a positive number, or why not.
fn size(f: &Feature, name: &str) -> Result<f64, String> {
    let x = f
        .parameters
        .get(name)
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("its record gives no {name}"))?;
    if x.is_finite() && x > 0.0 {
        Ok(x)
    } else {
        Err(format!("its {name} is {x}, not a positive size"))
    }
}

/// A recognised fillet or chamfer as the grammar reads it, or why it cannot be stated.
fn edge<'a>(cx: &Context, f: &'a Feature) -> Result<Edge<'a>, String> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let frame = &cx.frame;
    let mut at = point(f.parameters.get(if f.kind == "cylindrical_profile" {
        "axis_point"
    } else {
        "at"
    }))
    .map(|l| frame.point(l))
    .ok_or("its record gives no point on it (at)")?;
    let turned = f.parameters.get("turned").and_then(Value::as_bool) == Some(true);
    let mut oriented = f.kind == "oriented_chamfer";
    let axis = if oriented {
        crate::oriented_chamfers::read(cx.analysis, f)?.view_axis
    } else {
        let given = f.parameters.get("axis").ok_or("its record gives no axis")?;
        match frame.letter(given) {
            Some(d) => principal(d).ok_or("its axis is not along x, y or z in the part's frame")?,
            // An oblique fillet (quiddity-rust#120: a unit-vector axis) is read like an oriented
            // chamfer: its radius, a true size, is called out where its edge is seen most nearly
            // end-on, and counted with equal principal fillets when there are any.
            None if matches!(f.kind.as_str(), "fillet" | "cylindrical_profile") && !turned => {
                let d = crate::lint::parameters::direction(&holes::frame(cx.analysis), Some(given))
                    .filter(|d| d.iter().all(|c| c.is_finite()) && d.iter().any(|c| *c != 0.0))
                    .ok_or("its axis is neither x, y, z nor a direction")?;
                oriented = principal(d).is_none();
                match principal(d) {
                    Some(k) => k,
                    None => super::holes::oblique_axes(d)[0],
                }
            }
            None => return Err("its axis is not x, y or z".into()),
        }
    };
    if f.kind == "cylindrical_profile" && oriented {
        // An oblique cylinder projects as an ellipse. Its axis point can be far
        // outside the part (cgb243's shallow R1412.919 patch), so lead to a proven
        // point on the trimmed surface rather than to that off-part centre.
        at = cx
            .analysis
            .radius_arcs
            .get(&f.id)
            .into_iter()
            .flatten()
            .find_map(|arc| arc.tips.get(arc.tips.len() / 2).copied())
            .ok_or("its oblique cylinder has no proven trimmed surface site")?;
    }
    let m = |parameter: &str, value: f64| Measurement {
        feature: f.id.clone(),
        parameter: parameter.into(),
        value,
    };
    if matches!(f.kind.as_str(), "fillet" | "cylindrical_profile") {
        let parameter = if f.kind == "fillet" {
            "fillet.radius"
        } else {
            "cylindrical_profile.radius"
        };
        let r = size(f, "radius")?;
        let label = format!("R{}", fmt(r));
        return Ok(Edge {
            feature: f,
            parameter,
            axis,
            turned,
            oriented,
            at,
            value: r,
            same: label.bytes().map(i64::from).collect(),
            terms: vec![label],
            measurements: vec![m(parameter, r)],
        });
    }
    let (leg1, leg2, angle) = (size(f, "leg1")?, size(f, "leg2")?, size(f, "angle")?);
    let parameter = if oriented {
        "oriented_chamfer.length"
    } else {
        "chamfer.length"
    };
    let mut measurements = vec![m(parameter, leg1)];
    let terms = if c_form(leg1, leg2, angle) {
        vec![format!("C{}", fmt(leg1))]
    } else {
        measurements.push(m(
            if oriented {
                "oriented_chamfer.angle"
            } else {
                "chamfer.angle"
            },
            angle,
        ));
        vec![
            fmt(leg1),
            "×".into(),
            format!("{}°", crate::dims::fmt(angle)),
        ]
    };
    Ok(Edge {
        feature: f,
        parameter,
        axis,
        turned,
        oriented,
        at,
        value: leg1,
        // draftwright's key: the legs to 1e-3 mm and the angle to 0.01°.
        same: vec![
            (leg1 * 1e3).round() as i64,
            (leg2 * 1e3).round() as i64,
            (angle * 1e2).round() as i64,
        ],
        terms,
        measurements,
    })
}

/// The point on the axis a treatment turned about world axis *k*, through *at*, of size *size*
/// turns about: that of the coaxial turned band whose span (widened by the treatment's size)
/// contains its station and whose radius reaches it, the nearest such; else that of a part turned
/// whole (*whole*) about *k*; else none. The record names only an axis letter, not the
/// treatment's own axis line, so a turned treatment about a parallel off-axis hole that lies
/// inside a band's radius would be turned about that band's axis. No corpus part shows one
/// (the corpus parts with bands, ctc05 and GRM-03, are coaxial); the radius is not
/// required to match a band's, since a bevel at a bore's mouth rightly turns about the shaft.
fn turning_axis(bands: &[Band], whole: Option<&Turned>, k: usize, at: P3, size: f64) -> Option<P3> {
    crate::lint::turned::coaxial(bands, k, at, size)
        .map(|b| b.on)
        .or_else(|| whole.filter(|t| t.axis == k).map(|t| t.on))
}

/// Where the leader points: the record's point *at*, or for a treatment turned about the axis
/// line through *on* along *axis*, that point turned about the axis onto the profile, on the side
/// it already lies. The site is set at the treatment's radius from the axis along both transverse
/// axes, so it projects onto the profile in either profile view, whichever placement uses (only
/// its projection is used).
fn site(on: Option<P3>, at: P3, axis: usize) -> P3 {
    let Some(on) = on else {
        return at;
    };
    let (j, k) = ((axis + 1) % 3, (axis + 2) % 3);
    let r = (at[j] - on[j]).hypot(at[k] - on[k]);
    let mut p = at;
    for c in [j, k] {
        let sign = if at[c] - on[c] < -1e-9 { -1.0 } else { 1.0 };
        p[c] = on[c] + sign * r;
    }
    p
}

/// A radius callout's (fillet or blend) leader sites in view *view* once it is chosen, when a site
/// misses the middle half of every arc the view sees end-on: its members' trimmed arcs that the
/// view sees end-on ([`crate::radius`]'s evidence), so the tip lands well on a drawn arc, never at
/// its tangent point with a straight edge, where the leader reads as pointing at the edge
/// (maintainer 2026-10-25). A callout whose sites all lie in such arcs' middle halves, or none of
/// whose members shows an arc there, keeps its sites.
pub(super) fn route_profile(analysis: &crate::model::Analysis, w: &mut Want, view: &str) {
    let Shape::Callout { sites, arcs } = &mut w.shape else {
        return;
    };
    let Some((_, toward)) = super::TOWARD.iter().find(|(n, _)| *n == view) else {
        return;
    };
    if !arcs.is_empty()
        || !w.measurements.iter().any(|m| {
            matches!(
                m.parameter.as_str(),
                "fillet.radius" | "blend.radius" | "cylindrical_profile.radius"
            )
        })
    {
        return;
    }
    // Any isometric projection along the line of sight serves to test a point against an arc.
    let k = (0..3).find(|&k| toward[k] != 0.0).unwrap_or(0);
    let (a, b) = ((k + 1) % 3, (k + 2) % 3);
    let page = |p: P3| [p[a], p[b]];
    let mut shown = Vec::new();
    for m in w.measurements.iter().filter(|m| {
        matches!(
            m.parameter.as_str(),
            "fillet.radius" | "blend.radius" | "cylindrical_profile.radius"
        )
    }) {
        if let Ok(more) = crate::radius::projected(analysis, &m.feature, *toward, page) {
            shown.extend(more);
        }
    }
    if shown.is_empty() {
        return;
    }
    if sites.iter().all(|(p, r)| {
        *r == 0.0
            && shown
                .iter()
                .any(|arc| arc.on_arc(page(*p)) && arc.middle(page(*p)))
    }) {
        return;
    }
    // The middle half of each arc the view sees end-on: the leader's tip is set on it towards its
    // elbow ([`super::leaders`]' rim tip), radially when the elbow lies within its sweep.
    let mut middles = Vec::new();
    for m in w.measurements.iter().filter(|m| {
        matches!(
            m.parameter.as_str(),
            "fillet.radius" | "blend.radius" | "cylindrical_profile.radius"
        )
    }) {
        for arc in analysis.radius_arcs.get(&m.feature).into_iter().flatten() {
            if crate::radius::seen_end_on(arc.axis, *toward) {
                let mut middle = arc.arc.clone();
                middle.start += 0.25 * middle.sweep;
                middle.sweep *= 0.5;
                middles.push(middle);
            }
        }
    }
    if !middles.is_empty() {
        *arcs = middles;
    }
}

/// A group's key: its family (chamfers 0, fillets 1) and what its members share ([`Edge::same`]).
type Key = (u8, Vec<i64>);

/// The fillets' and chamfers' callouts, with the problems met on the way.
fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let analysis = cx.analysis;
    let mut problems = Vec::new();
    // A group is one requirement: fillets that print the same radius, chamfers with the same
    // legs and angle.
    let mut groups: BTreeMap<Key, Vec<Edge>> = BTreeMap::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| !crate::lint::blends::is_relief(analysis, f))
    {
        let family = match f.kind.as_str() {
            "chamfer" | "oriented_chamfer" => 0,
            "fillet" => 1,
            "cylindrical_profile" => 1,
            _ => continue,
        };
        match edge(cx, f) {
            Ok(e) => groups.entry((family, e.same.clone())).or_default().push(e),
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
    // Chamfers before fillets, as draftwright's orchestrator runs them; within each, smallest
    // first.
    let mut groups: Vec<(Key, Vec<Edge>)> = groups.into_iter().collect();
    groups.sort_by_cached_key(|((family, same), members)| {
        let least = members.iter().map(|e| key(e.value)).min();
        (*family, least, same.clone())
    });

    let rank = |id: &str| analysis.features.iter().position(|f| f.id == id);
    let bands = crate::lint::turned::all_bands(analysis);
    let mut out = Vec::new();
    for ((family, _), members) in groups {
        // The presentation the leader points into: the most populous axis and view.
        let mut by: BTreeMap<(&str, &str), Vec<&Edge>> = BTreeMap::new();
        // Keep the established principal fillet's presentation when a residual profile
        // has the same radius. Turned inside rounds were already fillets before v0.2.5
        // corrected their side; that correction must not move their valid radius leader.
        // Every surface still carries its own measurement claim.
        let preferred = |e: &Edge| {
            e.feature.kind == "fillet"
                && !e.oriented
                && (e.turned
                    || e.feature.parameters.get("side").and_then(Value::as_str) != Some("concave"))
        };
        let has_preferred = members.iter().any(preferred);
        let has_fillet = members
            .iter()
            .any(|e| e.feature.kind == "fillet" && !e.oriented);
        let has_principal = members.iter().any(|e| !e.oriented);
        for e in members.iter().filter(|e| {
            if has_preferred {
                preferred(e)
            } else if has_fillet {
                e.feature.kind == "fillet" && !e.oriented
            } else {
                !has_principal || !e.oriented
            }
        }) {
            by.entry((AXES[e.axis], e.views()[0])).or_default().push(e);
        }
        let (_, visible) = by
            .iter()
            .min_by_key(|(k, v)| (std::cmp::Reverse(v.len()), **k))
            // Invariant: A group is created only when its first member is inserted.
            .expect("a group has a member");
        // Its representative, which names the item: the member first by its point, as
        // draftwright sorts by `frame.origin` (then by feature order, for a tie).
        let first = visible
            .iter()
            .min_by(|a, b| {
                (a.at.iter().zip(&b.at).map(|(x, y)| x.total_cmp(y)))
                    .find(|o| o.is_ne())
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| rank(&a.feature.id).cmp(&rank(&b.feature.id)))
            })
            // Invariant: Presentations are built from nonempty member groups.
            .expect("a presentation has a member");
        let views = first.views();
        let n = members.len();
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        terms.extend(first.terms.iter().cloned());
        let (noun, size, seen) = if family == 0 {
            ("chamfer", "size", "its bevel is seen edge-on")
        } else if first.feature.kind == "cylindrical_profile" {
            (
                "cylindrical profile",
                "radius",
                "its surface is seen as an arc",
            )
        } else {
            ("fillet", "radius", "its rounded edge is seen as an arc")
        };
        let mut basis = match (n, first.turned) {
            (1, false) => format!("a {noun}'s {size} is called out where {seen}"),
            (1, true) => format!("a turned {noun}'s {size} is called out in the shaft's profile"),
            (_, false) => format!(
                "equal {noun}s are called out once and counted, where {seen} (draftwright's n× callout)"
            ),
            (_, true) => format!(
                "equal turned {noun}s are called out once and counted, in the shaft's profile (draftwright's n× callout)"
            ),
        };
        if family == 0 && first.terms.len() > 1 {
            basis += "; its legs differ or its angle is not 45°, so it states its first leg and its angle";
        }
        if members.iter().any(|e| e.oriented) {
            basis += if family == 0 {
                "; oblique-edge bevels use their true support-plane size and angle, not a foreshortened projection"
            } else {
                "; an oblique fillet's radius is its true size, called out where its edge is seen most nearly end-on"
            };
        }
        let elsewhere = n - visible.len();
        if elsewhere > 0 {
            basis += &format!(
                "; {elsewhere} of them lie along another axis or read in another view and are counted here, as draftwright counts them"
            );
        }
        let sites = visible
            .iter()
            .map(|e| {
                let on = (e.turned && !e.oriented)
                    .then(|| turning_axis(&bands, cx.turned.as_ref(), e.axis, e.at, e.value))
                    .flatten();
                (site(on, e.at, e.axis), 0.0)
            })
            .collect();
        out.push(Want {
            units: cx.spec.units,
            id: super::want_id(&first.feature.id, first.parameter),
            views,
            value: first.value,
            terms,
            measurements: members
                .iter()
                .flat_map(|e| e.measurements.iter().cloned())
                .collect(),
            basis,
            shape: Shape::Callout {
                sites,
                arcs: Vec::new(),
            },
            turning: None,
        });
    }
    (out, problems)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// draftwright's `_fmt_chamfer`: equal legs (within 0.05 mm) at 45° (within 0.5°) are `C`.
    #[test]
    fn the_c_form_is_equal_legs_at_45_degrees() {
        assert!(c_form(5.0, 5.0, 45.0));
        assert!(c_form(5.0, 5.04, 45.4));
        assert!(!c_form(5.0, 5.06, 45.0));
        assert!(!c_form(5.0, 5.0, 45.6));
        assert!(!c_form(31.75, 14.805, 25.0));
    }

    /// ISSUE-1276-a: a turned treatment's leader site is set at its radius off its turning axis
    /// along both transverse axes, keeping its axial station and side, so it projects onto the
    /// profile in either profile view (front shows x, side y); with no turning axis it keeps its
    /// physical point.
    #[test]
    fn a_turned_site_turns_onto_the_profile() {
        let on = [1.0, 2.0, 0.0];
        // 3-4-5 off the axis, behind the front view's plane.
        let at = [1.0 + 3.0, 2.0 + 4.0, 7.0];
        assert_eq!(site(Some(on), at, 2), [6.0, 7.0, 7.0]);
        let below = [1.0 - 3.0, 2.0 + 4.0, 7.0];
        assert_eq!(site(Some(on), below, 2), [-4.0, 7.0, 7.0]);
        assert_eq!(site(None, at, 2), at);
    }

    /// ISSUE-1276-a: the turning axis is the coaxial band's whose span holds the treatment's
    /// station, not only a part turned whole; a parallel shaft elsewhere does not attract it.
    #[test]
    fn a_treatment_turns_about_the_band_that_holds_it() {
        let band = |on: P3, lo: f64, hi: f64, diameter: f64| Band {
            axis: 2,
            on,
            lo,
            hi,
            diameter,
        };
        let shaft = band([0.0, 0.0, 279.4], 279.4, 482.6, 63.5);
        let other = band([200.0, 0.0, 0.0], 0.0, 500.0, 63.5);
        let bands = [other, shaft];
        let at = [0.0, -28.575, 479.425];
        let on = turning_axis(&bands, None, 2, at, 6.35);
        assert_eq!(on, Some([0.0, 0.0, 279.4]));
        assert_eq!(site(on, at, 2), [28.575, -28.575, 479.425]);
        // Beyond every band's span, or about another axis: no turning axis.
        assert_eq!(
            turning_axis(&bands, None, 2, [0.0, -28.575, 600.0], 6.35),
            None
        );
        assert_eq!(turning_axis(&bands, None, 0, at, 6.35), None);
    }
}
