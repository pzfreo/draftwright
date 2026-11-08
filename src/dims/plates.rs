//! Plate and wall thicknesses in exposed boundary profiles. Python's fixed
//! axis map is the fallback; proven alternative statements cover derived intervals.

use super::{Context, Measurement, Omitted, Planner, Problem, Shape, Want, fmt, want_id};
use crate::items::State;
use crate::lint::plates;
use crate::model::Analysis;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["plate"],
    wants,
};

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for f in cx.analysis.features.iter().filter(|f| f.kind == "plate") {
        let p = match plates::read(cx.analysis, f) {
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
        let k = p.axis;
        let value = p.thickness();
        out.push(Want {
            id: want_id(&f.id, "thickness.length"),
            views: match k {
                0 => vec!["front", "plan"],
                1 => vec!["side", "plan"],
                _ => vec!["front", "side"],
            },
            value,
            terms: vec![fmt(value)],
            measurements: vec![Measurement {
                feature: f.id.clone(),
                parameter: "thickness.length".into(),
                value,
            }],
            basis: "a plate's thickness, face to face in the view showing its thin profile".into(),
            shape: Shape::Linear {
                a: p.span[0],
                b: p.span[1],
                axis: k,
                a_edge: true,
                b_edge: true,
                order: 0,
            },
            turning: None,
        });
    }
    (out, problems)
}

type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);

/// Covered items keep their IDs. Only actually retained owner measurements count;
/// omitting an alternative restores the plate's own dimension.
pub(super) fn consolidate(a: &Analysis, kept: &mut Vec<Kept>, omitted: &mut Vec<Omitted>) {
    let mut gone = Vec::new();
    let mut borrowed = std::collections::BTreeSet::new();
    for (i, (w, ..)) in kept.iter().enumerate() {
        let Some(m) = w
            .measurements
            .iter()
            .find(|m| m.parameter == "thickness.length")
        else {
            continue;
        };
        let Some(f) = a.features.iter().find(|f| f.id == m.feature) else {
            continue;
        };
        let intrinsic = plates::exclusion(a, f);
        let alternative = plates::alternatives(a, f).into_iter().find_map(|terms| {
            let owners: Option<Vec<_>> = terms
                .iter()
                .map(|t| {
                    kept.iter()
                        .find(|(owner, ..)| {
                            owner.id != w.id
                                && owner.measurements.iter().any(|m| {
                                    m.feature == t.feature
                                        && m.parameter == t.parameter
                                        && (m.value - t.value).abs() <= 1e-3
                                })
                        })
                        .map(|(owner, ..)| owner.id.clone())
                })
                .collect();
            owners.filter(|o| !o.is_empty())
        });
        if intrinsic.is_some() || alternative.is_some() {
            if let Some(owners) = &alternative {
                borrowed.extend(owners.iter().cloned());
            }
            let by = alternative
                .as_ref()
                .and_then(|o| o.first())
                .cloned()
                // Invariant: The preceding branch requires alternative or intrinsic to be Some.
                .unwrap_or_else(|| intrinsic.as_ref().unwrap().by.clone());
            omitted.push(Omitted {
                id: w.id.clone(),
                label: w.terms.join(" "),
                basis: intrinsic.as_ref().map_or_else(
                    || {
                        format!(
                            "conveyed by {by}: the proven interval is already stated; dependencies: {}",
                            // Invariant: The preceding is_some_and predicate required alternative to be Some.
                            alternative.unwrap().join(", ")
                        )
                    },
                    |proof| format!("conveyed by {by}: recognised ownership ({})", proof.reason),
                ),
                state: State::Covered { by },
            });
            gone.push(i);
        }
    }
    // A top slab's explicit size and the overall height own its underside station.
    // Keep a station another plate borrowed; never leave a covered item without its proof.
    if a.principal_profiles.len() == 1 {
        for (i, (w, ..)) in kept.iter().enumerate() {
            let Shape::Linear {
                a: lo,
                b: hi,
                axis: 2,
                ..
            } = w.shape
            else {
                continue;
            };
            if gone.contains(&i)
                || !w
                    .measurements
                    .iter()
                    .any(|m| m.parameter == "thickness.length")
                || (hi[2] - a.envelope[1][2]).abs() > 1e-3
            {
                continue;
            }
            let Some((overall, ..)) = kept.iter().find(|(d, ..)| {
                d.measurements
                    .iter()
                    .any(|m| m.feature == "envelope" && m.parameter == "height.length")
            }) else {
                continue;
            };
            for (j, (rung, ..)) in kept.iter().enumerate() {
                if gone.contains(&j) || borrowed.contains(&rung.id) {
                    continue;
                }
                if rung.measurements.iter().any(|m| {
                    m.feature == "step_level"
                        && m.parameter.starts_with("step_height.length.")
                        && (m.value - (lo[2] - a.envelope[0][2])).abs() <= 1e-3
                }) {
                    omitted.push(Omitted {
                        id: rung.id.clone(), label: rung.terms.join(" "),
                        basis: format!("conveyed by {}: the underside station follows from the top slab and overall height; dependencies: {}, {}", w.id, w.id, overall.id),
                        state: State::Covered { by: w.id.clone() },
                    });
                    gone.push(j);
                }
            }
        }
    }
    let mut i = 0;
    kept.retain(|_| {
        let retain = !gone.contains(&i);
        i += 1;
        retain
    });
}

/// Equal thicknesses on one body share a counted statement, including walls on
/// different axes. Distinct authored bands stay distinct; unrelated bodies never join.
pub(super) fn share(
    a: &Analysis,
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
) {
    let body = |w: &Want| {
        let m = w
            .measurements
            .iter()
            .find(|m| m.parameter == "thickness.length")?;
        a.features
            .iter()
            .find(|f| f.id == m.feature)?
            .parameters
            .get("body_key")
            .filter(|key| !key.is_null())
            .cloned()
    };
    let mut gone = std::collections::BTreeSet::new();
    for i in 0..kept.len() {
        if gone.contains(&i) {
            continue;
        }
        let Some(key) = body(&kept[i].0) else {
            continue;
        };
        let mut group: Vec<_> = (i..kept.len())
            .filter(|j| {
                !gone.contains(j)
                    && body(&kept[*j].0).as_ref() == Some(&key)
                    && (kept[*j].0.value - kept[i].0.value).abs() <= 1e-6
                    && kept[*j].0.terms == kept[i].0.terms
            })
            .collect();
        if group.len() < 2 {
            continue;
        }
        group.sort_by(|x, y| {
            kept[*x].1.cmp(&kept[*y].1).then_with(|| {
                super::presentation_id(a, &kept[*x].0).cmp(&super::presentation_id(a, &kept[*y].0))
            })
        });
        let winner = group[0];
        let by = kept[winner].0.id.clone();
        let count = group.len();
        for loser in group.into_iter().skip(1) {
            omitted.push(Omitted {
                id: kept[loser].0.id.clone(),
                label: kept[loser].0.terms.join(" "),
                basis: format!(
                    "conveyed by {by}: equal plate thicknesses on one body are stated once, counted"
                ),
                state: State::Covered { by: by.clone() },
            });
            let claims = kept[loser].0.measurements.clone();
            kept[winner].0.measurements.extend(claims);
            gone.insert(loser);
        }
        kept[winner].0.terms.insert(0, format!("{count}×"));
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

pub(super) fn prefer_readable(
    analysis: &Analysis,
    wants: &mut [Want],
    frames: &std::collections::BTreeMap<&str, super::Frame>,
    spec: Option<&crate::model::Spec>,
) {
    for w in wants {
        if !w
            .measurements
            .iter()
            .any(|m| m.parameter == "thickness.length")
            || spec
                .and_then(|s| s.dimensions.get(&w.id))
                .is_some_and(|entry| {
                    entry.view.is_some() || entry.place.as_ref().is_some_and(|p| p.view.is_some())
                })
        {
            continue;
        }
        if matches!(w.shape, Shape::Linear { axis: 1, .. })
            && frames
                .get("side")
                .is_some_and(|f| w.value * f.scale() < 2.0 * super::ARROW)
            && frames.contains_key("plan")
        {
            w.views.retain(|v| *v != "plan");
            w.views.insert(0, "plan");
        }
        // A witness beside a hole centreline is ambiguous even when its slab is
        // physically valid. Prefer an available edge-on view without that coincidence.
        if let Shape::Linear { a, b, axis, .. } = w.shape {
            let holes: Vec<_> = crate::bridge::holes::read(analysis)
                .into_iter()
                .filter_map(|(_, h)| h.ok())
                .collect();
            let contacts = |name: &str| {
                let Some(frame) = frames.get(name) else {
                    return usize::MAX;
                };
                let normal = match name {
                    "front" => 1,
                    "plan" => 2,
                    _ => 0,
                };
                holes
                    .iter()
                    .filter(|h| {
                        h.principal.is_some_and(|(k, _)| k == normal)
                            && [a[axis], b[axis]]
                                .iter()
                                .any(|s| (h.at[axis] - s).abs() * frame.scale() <= 0.6)
                    })
                    .count()
            };
            if let Some(&first) = w.views.iter().find(|name| frames.contains_key(**name))
                && contacts(first) > 0
                && let Some(&clear) = w
                    .views
                    .iter()
                    .find(|name| frames.contains_key(**name) && contacts(name) == 0)
            {
                w.views.retain(|name| *name != clear);
                w.views.insert(0, clear);
                w.basis += "; carried in the view where neither thickness witness coincides with an end-on hole centreline";
            }
        }
        if let Shape::Linear {
            a,
            b,
            axis,
            a_edge,
            b_edge,
            ..
        } = &mut w.shape
        {
            let profile = w
                .views
                .iter()
                .filter_map(|name| {
                    let frame = frames.get(name)?;
                    let mut near: Option<(f64, [f64; 3], [f64; 3])> = None;
                    // Boundary faces prove visibility only for an isolated solid.
                    if analysis.principal_profiles.len() != 1 {
                        return None;
                    }
                    let (normal, high) = match *name {
                        "front" => (1, false),
                        "plan" => (2, true),
                        _ => (0, true),
                    };
                    let across = (0..3).find(|k| *k != *axis && *k != normal)?;
                    let feature = analysis
                        .features
                        .iter()
                        .find(|f| w.measurements.iter().any(|m| m.feature == f.id))?;
                    for source in &analysis.principal_profiles {
                        if let Some(key) = feature
                            .parameters
                            .get("body_key")
                            .and_then(|v| v.as_array())
                        {
                            if key.len() < 6 {
                                continue;
                            }
                            let local: Option<Vec<_>> =
                                key[..6].iter().map(|v| v.as_f64()).collect();
                            let Some(local) = local else {
                                continue;
                            };
                            let mut bounds = [[f64::INFINITY; 3], [f64::NEG_INFINITY; 3]];
                            for corner in 0..8 {
                                let q = std::array::from_fn(|k| local[k + 3 * ((corner >> k) & 1)]);
                                let q = crate::bridge::holes::to_part(
                                    &crate::bridge::holes::frame(analysis),
                                    q,
                                );
                                for k in 0..3 {
                                    bounds[0][k] = bounds[0][k].min(q[k]);
                                    bounds[1][k] = bounds[1][k].max(q[k]);
                                }
                            }
                            if (0..3).any(|k| {
                                (0..2).any(|end| {
                                    (bounds[end][k] - source.bounds[end][k]).abs() > 1e-3
                                })
                            }) {
                                continue;
                            }
                        }
                        for face in &source.faces {
                            if face.unsplit
                                || face.axis != normal
                                || (face.at - source.bounds[usize::from(high)][normal]).abs() > 1e-3
                            {
                                continue;
                            }
                            let edges: Vec<_> = face
                                .outer_lines
                                .iter()
                                .chain(face.inner.iter().flat_map(|wire| wire.edges.iter()))
                                .filter(|e| e.kind == crate::lint::profiles::Kind::Line)
                                .collect();
                            for left in &edges {
                                if (left.start[*axis] - a[*axis]).abs() > 1e-3
                                    || (left.end[*axis] - a[*axis]).abs() > 1e-3
                                {
                                    continue;
                                }
                                for right in &edges {
                                    if (right.start[*axis] - b[*axis]).abs() > 1e-3
                                        || (right.end[*axis] - b[*axis]).abs() > 1e-3
                                    {
                                        continue;
                                    }
                                    let lo = left.bounds[0][across].max(right.bounds[0][across]);
                                    let hi = left.bounds[1][across].min(right.bounds[1][across]);
                                    if hi - lo <= 1e-3 {
                                        continue;
                                    }
                                    let mut pa = *a;
                                    let mut pb = *b;
                                    pa[normal] = face.at;
                                    pb[normal] = face.at;
                                    // Anchor where both solid edges meet the silhouette, so
                                    // the witnesses leave the part without crossing it; take
                                    // the default strip's end when both ends reach it.
                                    let horizontal = super::placement::across(frame, *axis)?;
                                    let default =
                                        super::placement::thickness_strip(name, *axis, horizontal);
                                    let gap = |t: f64| {
                                        let mut q = pa;
                                        q[across] = t;
                                        super::placement::silhouette_gap(frame, q, horizontal)
                                    };
                                    let ends = [(lo, gap(lo)), (hi, gap(hi))];
                                    let reach = |(_, (side, d)): &&(f64, (_, f64))| {
                                        *d <= super::placement::ON_SILHOUETTE && *side == default
                                    };
                                    let anywhere = |e: &&(f64, (_, f64))| {
                                        e.1.1 <= super::placement::ON_SILHOUETTE
                                    };
                                    if let Some((t, _)) = ends
                                        .iter()
                                        .find(reach)
                                        .or_else(|| ends.iter().find(anywhere))
                                    {
                                        pa[across] = *t;
                                        pb[across] = *t;
                                        return Some((*name, pa, pb, 0.0));
                                    }
                                    // Otherwise remember the pair whose end lies nearest the
                                    // silhouette, in case no view has one that reaches it.
                                    let (t, (_, d)) = if ends[0].1.1 <= ends[1].1.1 {
                                        ends[0]
                                    } else {
                                        ends[1]
                                    };
                                    if near.is_none_or(|(best, ..)| d < best) {
                                        let (mut qa, mut qb) = (pa, pb);
                                        qa[across] = t;
                                        qb[across] = t;
                                        near = Some((d, qa, qb));
                                    }
                                }
                            }
                        }
                    }
                    near.map(|(d, pa, pb)| (*name, pa, pb, d))
                })
                .collect::<Vec<_>>();
            // A pair reaching the silhouette in any view wins, in view order; failing that, a
            // pair whose witnesses run over at most a short stretch of the part before leaving it.
            let profile = profile
                .iter()
                .find(|(.., d)| *d == 0.0)
                .or_else(|| {
                    profile
                        .iter()
                        .filter(|(.., d)| *d <= super::placement::NEAR_SILHOUETTE)
                        .min_by(|x, y| x.3.total_cmp(&y.3))
                })
                .copied();
            if let Some((view, pa, pb, _)) = profile {
                w.views.retain(|v| *v != view);
                w.views.insert(0, view);
                *a = pa;
                *b = pb;
                *a_edge = false;
                *b_edge = false;
                w.basis += "; both witnesses lie on solid edges of an exposed boundary profile";
                continue;
            }
            if *axis != 1 {
                continue;
            }
        }
        // A wall narrower than two arrowheads cannot carry inward arrows in
        // the end profile. The plan shows the same interval in its clear left band.
        if frames
            .get("side")
            .is_some_and(|f| w.value * f.scale() < 2.0 * super::ARROW)
            && frames.contains_key("plan")
        {
            w.views.retain(|v| *v != "plan");
            w.views.insert(0, "plan");
        }
    }
}

pub(super) fn default_terms(a: &Analysis, wants: &mut [Want], chosen: &[Option<&'static str>]) {
    let mut kept: Vec<_> = wants
        .iter()
        .zip(chosen)
        .map(|(w, v)| (w.clone(), true, *v, Vec::new()))
        .collect();
    let mut omitted = Vec::new();
    consolidate(a, &mut kept, &mut omitted);
    let mut given = vec![Vec::new(); kept.len()];
    share(a, &mut kept, &mut given, &mut omitted);
    for (w, ..) in kept {
        if let Some(raw) = wants.iter_mut().find(|raw| raw.id == w.id) {
            raw.terms = w.terms;
        }
    }
}
