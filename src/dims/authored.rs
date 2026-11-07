//! Spec-authored quantities use the ordinary Want -> lanes -> placement -> ink pipeline.
use super::{Measurement, Shape, Want, fmt};
use crate::model::dimension_geometry::{Reference, dot, sub, unit};
use crate::model::{Analysis, Invalid, Problem, Spec};

fn invalid(message: impl Into<String>) -> Invalid {
    Invalid {
        path: None,
        also: Vec::new(),
        message: message.into(),
        choices: vec![],
    }
}

pub(crate) fn check(s: &Spec) -> Result<(), Invalid> {
    for (id, d) in &s.dimensions {
        let authored = d.between.is_some() || d.kind.is_some() || d.along.is_some();
        if !authored {
            if id.starts_with("d:authored:") {
                return Err(invalid(format!(
                    "{id} needs between and kind (linear, diameter, radius or angle); remove the entry to delete it."
                )));
            }
            continue;
        }
        if !id.strip_prefix("d:authored:").is_some_and(|suffix| {
            !suffix.is_empty()
                && suffix
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
        }) {
            return Err(invalid(format!(
                "{id}: authored dimensions need a stable d:authored:<name> ID (letters, digits, _ or -)."
            )));
        }
        let kind = d.kind.as_deref().unwrap_or("linear");
        let count = match kind {
            "linear" | "angle" => 2,
            "diameter" | "radius" => 1,
            _ => {
                return Err(invalid(format!(
                    "{id}: kind must be linear, diameter, radius or angle."
                )));
            }
        };
        if !d
            .between
            .as_ref()
            .is_some_and(|refs| refs.len() == count && refs.iter().all(|r| !r.is_empty()))
        {
            return Err(invalid(format!(
                "{id}: {kind} needs {count} reference(s) in between; copy keys from Analysis.edge_ink.references."
            )));
        }
        if d.along
            .as_ref()
            .is_some_and(|a| kind != "linear" || !["x", "y", "z", "true"].contains(&a.as_str()))
        {
            return Err(invalid(format!(
                "{id}: along is x, y, z or true, and applies only to linear dimensions."
            )));
        }
        if d.fit.is_some() && !["linear", "diameter"].contains(&kind) {
            return Err(invalid(format!(
                "{id}: a fit qualifies a linear size or a diameter; give this radius or angle an explicit tolerance."
            )));
        }
    }
    Ok(())
}

pub(super) fn append(
    a: &Analysis,
    s: &Spec,
    wants: &mut Vec<Want>,
) -> Result<Vec<Problem>, Invalid> {
    if !s.dimensions.keys().any(|id| id.starts_with("d:authored:"))
        && !s
            .dimensions
            .values()
            .any(|d| d.between.is_some() || d.kind.is_some() || d.along.is_some())
    {
        return Ok(super::schemes::check_authored(s, wants));
    }
    let mut problems = Vec::new();
    check(s)?;
    for (id, d) in s.dimensions.iter().filter(|(_, d)| d.between.is_some()) {
        // Invariant: This loop selects authored dimensions with between references.
        let refs = d.between.as_ref().unwrap();
        for reference in refs {
            let candidates = reference_features(a, reference);
            if candidates.len() > 1 {
                return Err(Invalid {
                    message: format!(
                        "dimensions/{id}: reference {reference} is ambiguous between features {}; name an explicit feature axis.",
                        candidates.iter().cloned().collect::<Vec<_>>().join(", ")
                    ),
                    choices: candidates.iter().map(|f| format!("axis:{f}")).collect(),
                    ..invalid("")
                });
            }
        }
        if s.binding
            .as_ref()
            .is_some_and(|b| b.source_sha256 != a.binding.source_sha256)
            && refs.iter().any(|r| r.starts_with("axis:f:"))
        {
            return Err(invalid(format!(
                "{id}: the Spec is bound to another STEP revision, so its feature-axis ID is unsafe; replace axis:f:<id> with the matching axis:face:v1:<fingerprint> from the current Analysis, or explicitly rebind the Spec."
            )));
        }
        let kind = d.kind.as_deref().unwrap_or("linear");
        let along = d.along.as_deref();
        let m = a
            .measure_references(refs, kind, along)
            .map_err(|why| invalid(format!("dimensions/{id}: {why}")))?;
        let (shape, views) = match kind {
            "linear" => {
                // Invariant: resolve returns witnesses for a successfully resolved linear measurement.
                let [p, q] = m.witnesses.unwrap();
                if let Some(k) = ["x", "y", "z"].iter().position(|k| Some(*k) == along) {
                    (
                        Shape::Linear {
                            a: p,
                            b: q,
                            axis: k,
                            a_edge: false,
                            b_edge: false,
                            order: 0,
                        },
                        super::envelope::profile_views(k),
                    )
                } else {
                    let views = super::TOWARD
                        .iter()
                        .filter(|(_, t)| dot(sub(q, p), *t).abs() < 1e-6)
                        .map(|(v, _)| *v)
                        .collect();
                    (Shape::Aligned { a: p, b: q }, views)
                }
            }
            "angle" => {
                // Invariant: resolve returns an angle for a successfully resolved angular measurement.
                let angle = m.angle.unwrap();
                let views = angle
                    .principal()
                    .map(|k| vec![super::end_on(k)])
                    .unwrap_or_default();
                (
                    Shape::Angular {
                        angle,
                        alternates: Vec::new(),
                    },
                    views,
                )
            }
            _ => {
                if m.sites.is_empty() {
                    return Err(invalid(format!(
                        "{id}: the face has no proven leader attachment; choose another face."
                    )));
                }
                // Exact surface sites, including oblique cylinders: a size leader need not be end-on.
                let views = match a.dimension_reference(&refs[0]).map_err(invalid)? {
                    Reference::Cylinder { axis, .. } => {
                        let k = (0..3).find(|&k| axis[k].abs() > 1.0 - 1e-8);
                        k.map(|k| vec![super::end_on(k)])
                            .unwrap_or_else(|| super::NAMES.to_vec())
                    }
                    _ => super::NAMES.to_vec(),
                };
                (
                    Shape::Callout {
                        arcs: Vec::new(),
                        sites: m.sites.iter().take(32).map(|&p| (p, 0.0)).collect(),
                    },
                    views,
                )
            }
        };
        if views.is_empty() {
            return Err(invalid(format!(
                "{id}: no principal view shows this quantity true; choose references lying in a principal plane, or along x, y or z."
            )));
        }
        let parameter = if kind == "linear" {
            format!("linear.{}", along.unwrap_or("true"))
        } else {
            kind.to_string()
        };
        let feature = format!("geometry:{}", refs.join("|"));
        let label = match kind {
            "diameter" => format!("⌀{}", fmt(m.value)),
            "radius" => format!("R{}", fmt(m.value)),
            "angle" => format!("{}°", fmt(m.value)),
            _ => fmt(m.value),
        };
        let mut w = Want {
            id: id.clone(),
            views,
            value: m.value,
            terms: vec![label],
            measurements: vec![Measurement {
                feature,
                parameter,
                value: m.value,
            }],
            basis: format!(
                "spec-authored: {kind} from {}; measured from exact model references",
                refs.join(" and ")
            ),
            shape,
            turning: None,
        };
        // A feature-to-feature span is relative: it does not anchor either feature.
        // Only attach an absolute location when the other reference is a datum or
        // non-feature station. Shared automatic claims are transferred by ownership.
        if let Some((axis, ..)) = linear_span(&w.shape)
            && refs
                .iter()
                .filter(|r| !reference_features(a, r).is_empty())
                .count()
                == 1
        {
            for feature in referenced_features(a, refs) {
                w.measurements.push(Measurement {
                    feature,
                    parameter: format!("location.length.{}", ["x", "y", "z"][axis]),
                    value: m.value,
                });
            }
        }
        // Only compare physical supports, never scalar equality alone.
        for existing in wants.iter().filter(|w| {
            !d.omit
                && !s.dimensions.get(&w.id).is_some_and(|d| d.omit)
                && (s.dimension_source == crate::model::DimensionSource::Auto
                    || s.dimensions.contains_key(&w.id))
        }) {
            if duplicate(a, &w, existing, refs, kind)
                && (kind != "linear" || same_features(a, refs, existing))
            {
                if !super::tolerance::same_requirement(
                    a,
                    s,
                    &w,
                    existing,
                    &referenced_features(a, refs),
                ) {
                    return Err(invalid(format!(
                        "{id} contradicts the requirement on {}; repeated measurements must have the compatible tolerances and fits.",
                        existing.id
                    )));
                }
                problems.push(Problem {
                    id: format!("p:duplicate_measurement:{id}:{}", existing.id),
                    code: "duplicate_measurement".into(), severity: "warning".into(),
                    message: format!("{} repeats a measurement already stated by {}; both truthful statements are retained.", super::wording_of(&w), super::wording_of(existing)),
                    subjects: vec![id.clone(), existing.id.clone()], ..Default::default()
                });
            }
        }
        wants.push(w);
    }
    problems.extend(super::schemes::check_authored(s, wants));
    Ok(problems)
}

fn linear_span(s: &Shape) -> Option<(usize, f64, f64)> {
    match s {
        Shape::Linear { a, b, axis, .. } => {
            Some((*axis, a[*axis].min(b[*axis]), a[*axis].max(b[*axis])))
        }
        Shape::Aligned { a, b } => {
            let delta = sub(*b, *a);
            let k = (0..3).find(|&k| (0..3).all(|j| j == k || delta[j].abs() < 1e-6))?;
            Some((k, a[k].min(b[k]), a[k].max(b[k])))
        }
        _ => None,
    }
}

fn duplicate(a: &Analysis, w: &Want, o: &Want, refs: &[String], kind: &str) -> bool {
    let close = |a: f64, b: f64| (a - b).abs() < 1e-5;
    if let (Some((k, p, q)), Some((j, r, s))) = (linear_span(&w.shape), linear_span(&o.shape)) {
        return k == j && close(p, r) && close(q, s);
    }
    if let (Shape::Aligned { a: p, b: q }, Shape::Aligned { a: r, b: s }) = (&w.shape, &o.shape) {
        let eq = |p: &[f64; 3], q: &[f64; 3]| (0..3).all(|k| close(p[k], q[k]));
        if (eq(p, r) && eq(q, s)) || (eq(p, s) && eq(q, r)) {
            return true;
        }
    }
    if o.measurements.iter().any(|m| {
        m.feature.starts_with("geometry:") && {
            let other: Vec<_> = m
                .feature
                .trim_start_matches("geometry:")
                .split('|')
                .collect();
            let mut r: Vec<_> = refs.iter().map(String::as_str).collect();
            r.sort();
            let mut other = other;
            other.sort();
            r == other
                && ((kind == "diameter" || kind == "radius")
                    && (m.parameter == "diameter" || m.parameter == "radius")
                    || m.parameter == w.measurements[0].parameter)
        }
    }) {
        return true;
    }
    if kind == "radius" || kind == "diameter" {
        let Ok(r) = a.dimension_reference(&refs[0]) else {
            return false;
        };
        let (bounds, radius) = match &r {
            Reference::Cylinder { bounds, radius, .. }
            | Reference::Sphere { bounds, radius, .. } => (*bounds, *radius),
            _ => return false,
        };
        let states_size = |m: &Measurement| {
            (m.parameter.contains("diameter") && close(m.value, 2.0 * radius))
                || (m.parameter.contains("radius") && close(m.value, radius))
        };
        if let (Reference::Cylinder { origin, axis, .. }, Some(k)) = (&r, o.turning)
            && axis[k].abs() > 1.0 - 1e-8
            && (0..3)
                .all(|j| j == k || close(origin[j], 0.5 * (a.envelope[0][j] + a.envelope[1][j])))
            && o.measurements
                .iter()
                .any(|m| m.feature == "envelope" && m.parameter == "od.diameter" && states_size(m))
        {
            return true;
        }
        let faces: Vec<_> = a
            .face_attachments
            .iter()
            .enumerate()
            .filter_map(|(i, f)| {
                f.bounds
                    .filter(|b| {
                        (0..3).all(|k| close(bounds[0][k], b[k]) && close(bounds[1][k], b[k + 3]))
                    })
                    .map(|_| i)
            })
            .collect();
        return o.measurements.iter().any(|m| {
            states_size(m)
                && a.features
                    .iter()
                    .find(|f| f.id == m.feature)
                    .is_some_and(|f| f.faces.iter().any(|i| faces.contains(i)))
        });
    }
    if let (Shape::Angular { angle: p, .. }, Shape::Angular { angle: q, .. }) = (&w.shape, &o.shape)
    {
        return close(w.value, o.value)
            && p.principal() == q.principal()
            && (0..3).all(|k| Some(k) == p.principal() || close(p.vertex[k], q.vertex[k]))
            && p.rays().zip(q.rays()).is_some_and(|(p, q)| {
                p.iter().all(|p| {
                    q.iter().any(|q| {
                        unit(*q)
                            .zip(unit(*p))
                            .is_some_and(|(p, q)| dot(p, q).abs() > 1.0 - 1e-6)
                    })
                })
            });
    }
    false
}

/// Feature-axis identities, including the stable face spelling of the same axis.
fn referenced_features(a: &Analysis, refs: &[String]) -> std::collections::BTreeSet<String> {
    refs.iter().flat_map(|r| reference_features(a, r)).collect()
}

fn reference_features(a: &Analysis, reference: &str) -> std::collections::BTreeSet<String> {
    let mut out = std::collections::BTreeSet::new();
    if let Some(key) = reference.strip_prefix("axis:") {
        if a.features.iter().any(|f| f.id == key) {
            out.insert(key.to_string());
        } else if let Ok(Reference::Cylinder { bounds, .. }) = a.dimension_reference(key) {
            for f in &a.features {
                // A compound can contain this face without defining one axis of its own.
                if f.faces.iter().any(|&i| {
                    a.face_attachments.get(i).is_some_and(|face| {
                        face.bounds.is_some_and(|b| {
                            (0..3).all(|k| {
                                (bounds[0][k] - b[k]).abs() < 1e-6
                                    && (bounds[1][k] - b[k + 3]).abs() < 1e-6
                            })
                        })
                    })
                }) && a
                    .dimension_reference(&format!("axis:{}", f.id))
                    .is_ok_and(|r| matches!(r, Reference::Edge { .. }))
                {
                    out.insert(f.id.clone());
                }
            }
        }
    }
    out
}

fn same_features(a: &Analysis, refs: &[String], other: &Want) -> bool {
    let named = referenced_features(a, refs);
    if named.is_empty() {
        // Untyped face/vertex spans keep their existing physical-support check.
        return true;
    }
    let Some(owner) = other.measurements.first() else {
        return false;
    };
    if let Some(refs) = owner.feature.strip_prefix("geometry:") {
        named == referenced_features(a, &refs.split('|').map(String::from).collect::<Vec<_>>())
    } else {
        // A shared automatic owner states every one of its measurements, not
        // just its first feature's role.
        named
            .iter()
            .all(|f| other.measurements.iter().any(|m| m.feature == *f))
    }
}

/// Hand only the numerically stated roles to a new owner. Compound riders remain required.
#[derive(Default)]
pub(super) struct Ownership {
    pub claims: std::collections::BTreeMap<String, Vec<Measurement>>,
    pub covered: std::collections::BTreeMap<String, String>,
}

pub(super) fn ownership(a: &Analysis, s: &Spec, wants: &[Want]) -> Ownership {
    let mut out = Ownership::default();
    if s.dimension_source != crate::model::DimensionSource::Authored {
        return out;
    }
    for w in wants.iter().filter(|w| w.id.starts_with("d:authored:")) {
        let Some(d) = s.dimensions.get(&w.id) else {
            continue;
        };
        let Some(refs) = &d.between else { continue };
        let kind = d.kind.as_deref().unwrap_or("linear");
        for original in wants
            .iter()
            .filter(|o| !o.id.starts_with("d:authored:") && !s.dimensions.contains_key(&o.id))
        {
            if !duplicate(a, w, original, refs, kind) {
                continue;
            }
            let stated: Vec<_> = original
                .measurements
                .iter()
                .filter(|m| {
                    (m.value - w.value).abs() < 1e-6
                        && match kind {
                            "linear" => matches!(
                                original.shape,
                                Shape::Linear { .. } | Shape::Aligned { .. }
                            ),
                            "angle" => m.parameter.contains("angle"),
                            "diameter" => m.parameter.contains("diameter"),
                            "radius" => m.parameter.contains("radius"),
                            _ => false,
                        }
                })
                .cloned()
                .collect();
            if !stated.is_empty() && stated.len() == original.measurements.len() {
                out.covered.insert(original.id.clone(), w.id.clone());
            }
            let claims = out.claims.entry(w.id.clone()).or_default();
            for m in stated {
                // Preserve shared claims unless another authored witness already
                // explicitly locates that feature. Coincidence alone does not
                // make two distinct features' explicit locations duplicates.
                if wants.iter().any(|other| {
                    other.id != w.id
                        && other.id.starts_with("d:authored:")
                        && other
                            .measurements
                            .iter()
                            .any(|c| c.feature == m.feature && c.parameter == m.parameter)
                }) {
                    continue;
                }
                if !w
                    .measurements
                    .iter()
                    .chain(claims.iter())
                    .any(|c| c.feature == m.feature && c.parameter == m.parameter)
                {
                    claims.push(m);
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_feature_owns_only_the_surface_sizes_its_label_actually_states() {
        let (_, a) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/mounting_plate.step"),
        )
        .unwrap();
        let mut s = crate::model::Spec::default();
        let (mut wants, ..) = super::super::wants(&a);
        let (key, radius, owner) = a
            .dimension_references()
            .iter()
            .find_map(|(key, rs)| {
                let [crate::model::dimension_geometry::Reference::Cylinder { radius, .. }] =
                    rs.as_slice()
                else {
                    return None;
                };
                let d: crate::model::DimensionSpec =
                    serde_json::from_value(serde_json::json!({"kind":"diameter","between":[key]}))
                        .unwrap();
                s.dimensions.insert("d:authored:test".into(), d);
                super::append(&a, &s, &mut wants.clone())
                    .unwrap()
                    .into_iter()
                    .find(|p| p.code == "duplicate_measurement")
                    .and_then(|p| p.subjects.get(1).cloned())
                    .map(|id| (key.clone(), *radius, id))
            })
            .unwrap();
        let plan = crate::model::plan_recovered(&a, &s).unwrap();
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "duplicate_measurement")
        );
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.id == "d:authored:test" && d.placed.is_some())
        );
        s.dimension_source = crate::model::DimensionSource::Authored;
        super::append(&a, &s, &mut wants).unwrap();
        let authored = wants.iter().find(|w| w.id == "d:authored:test").unwrap();
        let mut automatic = wants.iter().find(|w| w.id == owner).unwrap().clone();
        assert!(super::duplicate(
            &a,
            authored,
            &automatic,
            std::slice::from_ref(&key),
            "diameter"
        ));
        for m in &mut automatic.measurements {
            if m.parameter.contains("diameter") || m.parameter.contains("radius") {
                m.value = radius * 7.0;
            }
        }
        assert!(!super::duplicate(
            &a,
            authored,
            &automatic,
            &[key],
            "diameter"
        ));
    }
}
