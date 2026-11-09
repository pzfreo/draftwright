//! Replace automatic location ownership with an authored, acyclic station network.
use super::{AXES, Measurement, Shape, Want, fmt};
use crate::{
    dimension_scheme::invalid,
    model::{Analysis, Invalid, Spec},
};
use std::collections::{BTreeMap, BTreeSet};

pub(super) type Covered = BTreeMap<String, String>;

fn eligible(w: &Want, k: usize) -> bool {
    matches!(w.shape, Shape::Linear { axis, order: 1, .. } if axis == k)
        || (matches!(w.shape, Shape::Linear { axis, .. } if axis == k)
            && w.measurements
                .iter()
                .any(|m| m.parameter == "step.length" || m.parameter.contains("pitch.length")))
}
fn anchor(a: &Analysis, w: &Want, p: [f64; 3], k: usize) -> Option<String> {
    let mut keys = Vec::new();
    for m in &w.measurements {
        let mut members = BTreeSet::new();
        if expand(a, &m.feature, &mut members).is_ok() {
            let mut members: Vec<_> = members.into_iter().collect();
            members.sort_by_cached_key(|r| crate::geometry_ids::station_key(a, r));
            keys.extend(members);
        }
        keys.extend([
            m.feature.clone(),
            format!("start:{}", m.feature),
            format!("end:{}", m.feature),
        ]);
    }
    keys.extend(["min".into(), "max".into()]);
    keys.extend(a.dimension_references().keys().cloned());
    keys.into_iter().find(|r| {
        a.scheme_station(r, k)
            .is_ok_and(|q| (p[k] - q[k]).abs() < 1e-6)
    })
}
fn expand(a: &Analysis, r: &str, out: &mut BTreeSet<String>) -> Result<(), Invalid> {
    if let Some(p) = a
        .recognition
        .as_ref()
        .and_then(|r0| r0.patterns.iter().find(|p| p.id == r))
    {
        if p.arrangement == "bolt_circle" {
            return Err(invalid(format!(
                "{r} is circular; retain its PCD scheme or select individual member IDs for Cartesian locations."
            )));
        }
        out.extend(p.members.iter().cloned());
    } else if let Some(f) = a
        .features
        .iter()
        .find(|f| f.id == r && f.kind == "slot_pattern")
    {
        let members = f
            .parameters
            .get("slots")
            .and_then(|v| v.as_array())
            .ok_or_else(|| invalid(format!("{r} has no member evidence; analyse again.")))?;
        out.extend(members.iter().filter_map(|v| v.as_str().map(String::from)));
    } else if a
        .features
        .iter()
        .any(|f| f.id == r && f.kind == "turned_step")
    {
        out.insert(format!("start:{r}"));
        out.insert(format!("end:{r}"));
    } else {
        out.insert(r.into());
    }
    Ok(())
}

pub(super) fn apply(
    a: &Analysis,
    s: &Spec,
    wants: &mut Vec<Want>,
) -> Result<(Covered, Vec<crate::model::Problem>), Invalid> {
    let mut problems = Vec::new();
    let mut covered = Covered::new();
    if s.dimension_schemes.is_empty() {
        return Ok((covered, Vec::new()));
    }
    crate::dimension_scheme::check(s)?;
    crate::dimension_scheme::check_binding(a, s)?;
    let old = wants.clone();
    for (name, d) in &s.dimension_schemes {
        let fail = |why: String| {
            invalid(format!(
                "dimension_schemes/{name}: {why} Correct the reference or remove dimension_schemes/{name}."
            ))
        };
        // Invariant: Dimension scheme check permits only x, y and z axes.
        let k = AXES.iter().position(|&k| k == d.axis).unwrap();
        if a.solid_count.is_some_and(|n| n > 1) {
            return Err(fail("A scheme needs one carrying solid; select a single part before authoring its station network.".into()));
        }
        let mut refs = BTreeSet::new();
        for r in &d.targets {
            expand(a, r, &mut refs)?;
        }
        let selected: Vec<_> = old
            .iter()
            .filter(|w| {
                eligible(w, k)
                    && d.view.as_deref().is_none_or(|v| w.views.contains(&v))
                    && (d.targets.is_empty()
                        || w.measurements.iter().any(|m| {
                            d.targets.contains(&m.feature)
                                || refs.contains(&m.feature)
                                || refs.contains(&format!("start:{}", m.feature))
                        }))
            })
            .collect();
        if d.targets.is_empty() {
            for w in &selected {
                for m in &w.measurements {
                    if m.parameter == "step.length"
                        || a.recognition
                            .as_ref()
                            .is_some_and(|r| r.patterns.iter().any(|p| p.id == m.feature))
                        || a.features
                            .iter()
                            .any(|f| f.id == m.feature && f.kind == "slot_pattern")
                    {
                        // A counted pitch's drawn span is only one interval. Replacing it
                        // must still locate every physical member, including the far end.
                        let mut members = BTreeSet::new();
                        if expand(a, &m.feature, &mut members).is_ok() {
                            refs.extend(members);
                        }
                    }
                }
                if w.measurements.iter().any(|m| {
                    m.parameter.starts_with("location")
                        && (a
                            .recognition
                            .as_ref()
                            .is_some_and(|r| r.patterns.iter().any(|p| p.id == m.feature))
                            || a.features
                                .iter()
                                .any(|f| f.id == m.feature && f.kind == "slot_pattern"))
                }) {
                    continue;
                }
                if let Shape::Linear { a: p, b: q, .. } = w.shape {
                    let ends: &[[f64; 3]] = if w.measurements.iter().any(|m| {
                        m.parameter == "step.length" || m.parameter.contains("pitch.length")
                    }) {
                        &[p, q]
                    } else {
                        &[q]
                    };
                    for &p in ends {
                        refs.insert(anchor(a,w,p,k).ok_or_else(||fail(format!("{} has no stable station reference; analyse again or name explicit targets.", w.id)))?);
                    }
                }
            }
        }
        if refs.is_empty() {
            return Err(fail("This view axis has no location dimensions; name feature or fingerprint targets instead.".into()));
        }
        let datum = d.from.as_deref().unwrap_or("min");
        let origin = a.scheme_station(datum, k).map_err(fail)?;
        let view = d
            .view
            .as_deref()
            .or_else(|| selected.first().and_then(|w| w.views.first().copied()))
            .unwrap_or(if k == 1 { "plan" } else { "front" });
        // Invariant: Scheme view validation permits only entries in NAMES.
        let view = *super::NAMES.iter().find(|&&n| n == view).unwrap();
        // Invariant: Every NAMES entry has its corresponding fixed TOWARD camera.
        if super::TOWARD.iter().find(|(v, _)| *v == view).unwrap().1[k].abs() > 0.5 {
            return Err(fail(format!(
                "The {view} view looks along {}; choose a principal view showing that axis across the page.",
                d.axis
            )));
        }
        let mut sites: Vec<_> = refs
            .iter()
            .map(|r| a.scheme_station(r, k).map(|p| (r.clone(), p)).map_err(fail))
            .collect::<Result<_, _>>()?;
        sites.push((datum.into(), origin));
        // Coincident stations share one witness: use the member nearest the ordinary
        // exterior strip, so an extension does not traverse another row of the pattern.
        let (cross, high) = match (view, k) {
            ("plan", 0) => (1, true),
            ("plan", _) => (0, false),
            ("front", 0) | ("side", 1) => (2, false),
            ("front", _) => (0, true),
            _ => (1, true),
        };
        let station_keys: BTreeMap<_, _> = sites
            .iter()
            .map(|(r, _)| (r.clone(), crate::geometry_ids::station_key(a, r)))
            .collect();
        sites.sort_by(|a, b| {
            a.1[k]
                .total_cmp(&b.1[k])
                .then_with(|| {
                    if high {
                        b.1[cross].total_cmp(&a.1[cross])
                    } else {
                        a.1[cross].total_cmp(&b.1[cross])
                    }
                })
                .then(station_keys[&a.0].cmp(&station_keys[&b.0]))
        });
        sites.dedup_by(|a, b| (a.1[k] - b.1[k]).abs() < 1e-6);
        if sites.len() < 2 {
            return Err(fail(
                "All targets coincide with the datum; choose at least one distinct station.".into(),
            ));
        }
        let mut pairs = Vec::new();
        if d.scheme == "chain" {
            pairs.extend(sites.windows(2).map(|p| (p[0].clone(), p[1].clone())));
        } else {
            pairs.extend(
                sites
                    .iter()
                    .filter(|(_, p)| (p[k] - origin[k]).abs() > 1e-6)
                    .map(|p| ((datum.to_string(), origin), p.clone())),
            );
        }
        let mut generated = Vec::new();
        for ((from, p), (to, q)) in pairs {
            let id =
                crate::dimension_scheme::item_id(name, &crate::geometry_ids::station_key(a, &to));
            let token = id.rsplit('/').next().expect("scheme ID has a token");
            let value = if d.scheme == "ordinate" {
                q[k] - p[k]
            } else {
                (q[k] - p[k]).abs()
            };
            let claim = format!("scheme:{name}|{datum}|{from}|{to}");
            let mut measurements = vec![Measurement {
                feature: claim,
                parameter: format!("{}.{}", d.scheme, d.axis),
                value,
            }];
            // Attachment provenance uses existing feature ownership for witness routing.
            // It carries no size/location role and grants no manufacturing coverage.
            for reference in [&from, &to] {
                let feature = reference
                    .strip_prefix("start:")
                    .or_else(|| reference.strip_prefix("end:"))
                    .or_else(|| reference.strip_prefix("axis:"))
                    .unwrap_or(reference);
                if a.features.iter().any(|f| f.id == feature) {
                    measurements.push(Measurement {
                        feature: feature.into(),
                        parameter: format!("scheme_witness.{name}.{token}"),
                        value,
                    });
                }
            }
            generated.push(Want {
                id, views:vec![view], value, terms:vec![fmt(value)], measurements,
                basis:format!("spec dimension_schemes/{name}: {} along {} from {from} to {to}; one station network owns these locations",d.scheme,d.axis),
                shape:Shape::Linear {a:p,b:q,axis:k,a_edge:from=="min"||from=="max",b_edge:to=="min"||to=="max",order:1},
                turning:None
            });
        }
        // A parent pattern centre is constructed from its fully located physical members.
        // Keep the established requirement vocabulary without inventing a centre station.
        for w in &old {
            let Shape::Linear { axis, .. } = w.shape else {
                continue;
            };
            if axis != k || !w.views.contains(&view) {
                continue;
            }
            let parent = w.measurements.iter().any(|m| {
                m.parameter.starts_with("location")
                    && (d.targets.is_empty() || d.targets.contains(&m.feature))
                    && (a.recognition.as_ref().is_some_and(|r| {
                        r.patterns
                            .iter()
                            .any(|p| p.id == m.feature && p.arrangement != "bolt_circle")
                    }) || a
                        .features
                        .iter()
                        .any(|f| f.id == m.feature && f.kind == "slot_pattern"))
            });
            if parent {
                if let Some(p) = edited_duplicate(s, w)? {
                    problems.push(p);
                    continue;
                }
                // Invariant: The generated dimension was pushed immediately before recording its id.
                covered.insert(w.id.clone(), generated.last().unwrap().id.clone());
            }
        }
        wants.extend(generated);
    }
    problems.extend(complete_ownership(a, s, &old, wants, &mut covered)?);
    Ok((covered, problems))
}

pub(super) fn from_spec(w: &Want) -> Option<String> {
    w.measurements.iter().find_map(|m| {
        m.feature
            .strip_prefix("scheme:")?
            .split_once('|')
            .map(|(n, _)| format!("dimension_schemes/{n}"))
    })
}

/// Running ordinates use an origin circle and one endpoint arrow. Placement reserves the
/// conservative two-arrow envelope first, so changing the ink cannot steal a neighbour's room.
pub(super) fn ink(w: &Want, mut p: super::Placed) -> super::Placed {
    if !w
        .measurements
        .iter()
        .any(|m| m.feature.starts_with("scheme:") && m.parameter.starts_with("ordinate."))
        || p.arrows.len() != 2
        || p.lines.is_empty()
    {
        return p;
    }
    let near = p.lines[0][1];
    let distance = |q: [f64; 2]| (q[0] - near[0]).powi(2) + (q[1] - near[1]).powi(2);
    let i = usize::from(distance(p.arrows[1][0]) < distance(p.arrows[0][0]));
    let origin = p.arrows.remove(i)[0];
    const C: f64 = 0.9238795325112867;
    const S: f64 = 0.3826834323650898;
    const D: f64 = std::f64::consts::FRAC_1_SQRT_2;
    const RING: [[f64; 2]; 16] = [
        [1., 0.],
        [C, S],
        [D, D],
        [S, C],
        [0., 1.],
        [-S, C],
        [-D, D],
        [-C, S],
        [-1., 0.],
        [-C, -S],
        [-D, -D],
        [-S, -C],
        [0., -1.],
        [S, -C],
        [D, -D],
        [C, -S],
    ];
    for n in 0..RING.len() {
        let at = |j: usize| {
            [
                origin[0] + 0.7 * RING[j % RING.len()][0],
                origin[1] + 0.7 * RING[j % RING.len()][1],
            ]
        };
        p.lines.push([at(n), at(n + 1)]);
    }
    p
}

/// #78 dimensions may not close an authored network through a different pair of endpoints.
pub(super) fn check_authored(s: &Spec, wants: &[Want]) -> Vec<crate::model::Problem> {
    let mut problems = Vec::new();
    if s.dimension_schemes.is_empty() {
        return problems;
    }
    for w in wants.iter().filter(|w| {
        (w.id.starts_with("d:authored:") || w.id.starts_with("d:scheme:"))
            && !s.dimensions.get(&w.id).is_some_and(|d| d.omit)
    }) {
        let interval = |shape: &Shape| match shape {
            Shape::Linear { a, b, axis, .. } => Some((*axis, a[*axis], b[*axis])),
            Shape::Aligned { a, b } => (0..3)
                .find(|&k| (0..3).all(|j| j == k || (a[j] - b[j]).abs() < 1e-8))
                .map(|k| (k, a[k], b[k])),
            _ => None,
        };
        let Some((k, lo, hi)) = interval(&w.shape) else {
            continue;
        };
        let edges: Vec<_> = wants
            .iter()
            .enumerate()
            .filter(|(_, o)| o.id.starts_with("d:scheme:") && o.id != w.id)
            .filter_map(|(i, o)| {
                let (axis, a, b) = interval(&o.shape)?;
                (axis == k).then_some((crate::lint::redundancy::Span::new(k, a, b), i))
            })
            .collect();
        if crate::lint::redundancy::path(&edges, crate::lint::redundancy::Span::new(k, lo, hi))
            .is_some()
        {
            problems.push(super::warning("duplicate_measurement", &w.id, format!("{} repeats an interval stated by the authored dimension scheme; the measured statement is retained.", super::wording_of(w)), vec![w.id.clone()]));
        }
    }
    problems
}

fn has_edits(s: &Spec, w: &Want) -> bool {
    s.precision.contains_key(&w.id)
        || s.dimensions.get(&w.id).is_some_and(|d| {
            d.tolerance.is_some()
                || d.fit.is_some()
                || d.text.is_some()
                || d.style.is_some()
                || d.place.is_some()
                || d.view.is_some()
                || d.omit
        })
}

/// A scheme cannot silently weaken an edited owner's manufacturing requirement.
fn edited_duplicate(s: &Spec, w: &Want) -> Result<Option<crate::model::Problem>, Invalid> {
    if s.dimensions
        .get(&w.id)
        .is_some_and(|d| d.tolerance.is_some() || d.fit.is_some())
    {
        return Err(invalid(format!(
            "{} contradicts the scheme's general-tolerance intervals; remove its tolerance or fit, or choose disjoint targets.",
            w.id
        )));
    }
    Ok(has_edits(s, w).then(|| {
        super::warning(
            "duplicate_measurement",
            &w.id,
            format!(
                "{} retains its presentation edit alongside the scheme repeating its location.",
                super::wording_of(w)
            ),
            vec![w.id.clone()],
        )
    }))
}

/// Give authored links priority, then retain automatic controllers only while they
/// add a new interval. This considers the union of every named scheme on a view axis.
fn complete_ownership(
    a: &Analysis,
    s: &Spec,
    old: &[Want],
    wants: &mut [Want],
    covered: &mut Covered,
) -> Result<Vec<crate::model::Problem>, Invalid> {
    let mut problems = Vec::new();
    use crate::lint::redundancy::{Span, path};
    type Edges = Vec<(Span, usize)>;
    let mut graphs: BTreeMap<(usize, &'static str), Edges> = BTreeMap::new();
    for (i, w) in wants.iter().enumerate().skip(old.len()) {
        let Shape::Linear {
            a: p, b: q, axis, ..
        } = w.shape
        else {
            continue;
        };
        let span = Span::new(axis, p[axis], q[axis]);
        let edges = graphs.entry((axis, w.views[0])).or_default();
        edges.push((span, i));
    }
    for ((k, view), mut edges) in graphs {
        let mut order: Vec<_> = (0..old.len()).collect();
        order.sort_by_key(|&i| {
            let w = &old[i];
            (
                if w.id.starts_with("d:pmi:") {
                    0
                } else if crate::dims::is_envelope_extent(&w.id) {
                    1
                } else if w
                    .measurements
                    .iter()
                    .any(|m| m.parameter.contains("pitch.length"))
                {
                    2
                } else {
                    3
                },
                w.id.clone(),
            )
        });
        for i in order {
            let w = &old[i];
            if covered.contains_key(&w.id)
                || !w.views.contains(&view)
                || s.dimensions.get(&w.id).is_some_and(|d| d.omit)
                || s.dimension_source == crate::model::DimensionSource::Authored
                    && !s.dimensions.contains_key(&w.id)
            {
                continue;
            }
            let Shape::Linear {
                a: p, b: q, axis, ..
            } = w.shape
            else {
                continue;
            };
            if axis != k
                || w.turning.is_some()
                || w.measurements
                    .iter()
                    .any(|m| m.parameter.contains("diameter"))
            {
                continue;
            }
            let span = Span::new(k, p[k], q[k]);
            let text = w.terms.join(" ");
            let mut spans: Vec<_> = w
                .measurements
                .iter()
                .flat_map(|m| crate::lint::schemes::supports(a, &m.feature, &m.parameter, &text))
                .filter(|s| s.axis == k)
                .collect();
            spans.sort_by(|p, q| p.lo.total_cmp(&q.lo).then(p.hi.total_cmp(&q.hi)));
            spans.dedup_by(|p, q| p.same(*q));
            if spans.is_empty()
                && (eligible(w, k)
                    || crate::dims::is_envelope_extent(&w.id)
                    || w.id.starts_with("d:pmi:"))
            {
                spans.push(span);
            }
            if spans.is_empty() {
                continue;
            }
            let routes: Vec<_> = spans.iter().map(|&span| path(&edges, span)).collect();
            let authored = routes
                .iter()
                .flatten()
                .flatten()
                .copied()
                .find(|&j| j >= old.len());
            if let Some(owner) = authored {
                if let Some(p) = edited_duplicate(s, w)? {
                    problems.push(p);
                    continue;
                }
                if routes.iter().any(Option::is_none) {
                    problems.push(super::warning("duplicate_measurement", &w.id, format!("{} retains its full counted statement alongside a scheme repeating some of its intervals.", super::wording_of(w)), vec![w.id.clone(), wants[owner].id.clone()]));
                    continue;
                }
                if w.id.starts_with("d:pmi:")
                    || w.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
                {
                    return Err(invalid(format!(
                        "{} already states semantic PMI across the scheme network; choose disjoint targets or remove the conflicting scheme.",
                        w.id
                    )));
                }
                covered.insert(w.id.clone(), wants[owner].id.clone());
                for m in &w.measurements {
                    wants[owner].measurements.push(Measurement {
                        feature: format!("scheme_owns:{}", m.feature),
                        parameter: m.parameter.clone(),
                        value: m.value,
                    });
                }
            } else {
                edges.extend(spans.into_iter().map(|s| (s, i)));
            }
        }
    }
    Ok(problems)
}

#[cfg(test)]
mod station_identity_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn coincident_station_witness_and_digest_survive_reference_renaming() {
        let mut a: Analysis = serde_json::from_str(include_str!(
            "../../tests/fixtures/identity/m10020.analysis7.json"
        ))
        .unwrap();
        a.recognition = None;
        let mut first = a.features[0].clone();
        first.kind = "slot".into();
        first.id = "f:slot:f3".into();
        first.faces = vec![3];
        first.fingerprint.position = a.envelope[0];
        first.fingerprint.position[0] += 10.;
        let mut second = first.clone();
        second.id = "f:slot:f9".into();
        second.faces = vec![9];
        a.features = vec![first, second];
        let run = |a: &Analysis| {
            let s: Spec = serde_json::from_value(json!({"dimension_schemes":{"station":{
                "scheme":"baseline", "axis":"x", "view":"plan", "from":"min",
                "targets":a.features.iter().map(|f| f.id.clone()).collect::<Vec<_>>()
            }}}))
            .unwrap();
            let mut wants = vec![];
            apply(a, &s, &mut wants).unwrap();
            assert_eq!(wants.len(), 1);
            let w = &wants[0];
            let witness = w
                .measurements
                .iter()
                .find(|m| m.parameter.starts_with("scheme_witness."))
                .unwrap();
            let faces = a
                .features
                .iter()
                .find(|f| f.id == witness.feature)
                .unwrap()
                .faces
                .clone();
            let Shape::Linear { a, b, axis, .. } = w.shape else {
                panic!("linear scheme");
            };
            (w.id.clone(), faces, a, b, axis, w.value)
        };
        let before = run(&a);
        assert_eq!(before.1, vec![3]);
        // Reverse lexical order while retaining exactly the same physical stations.
        a.features[0].id = "z-last".into();
        a.features[1].id = "a-first".into();
        assert_eq!(run(&a), before);
    }
}
