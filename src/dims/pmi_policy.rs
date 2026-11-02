//! PMI drawing policy and common origin substitution for every location planner.
use super::*;
use crate::lint::location::{self, Edge};
use crate::model::PmiMode;
use crate::pmi::{Category, Reading};

thread_local! {
    static PLAN_DEPTH: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// Candidate sheets keep their existing layout grade. Completeness findings belong to the
/// finished public plan, after bounded recovery has selected its drawing.
pub(crate) fn planning<T>(run: impl FnOnce() -> T) -> T {
    struct Restore(usize);
    impl Drop for Restore {
        fn drop(&mut self) {
            PLAN_DEPTH.set(self.0);
        }
    }
    let before = PLAN_DEPTH.get();
    PLAN_DEPTH.set(before + 1);
    let _restore = Restore(before);
    run()
}

type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);
pub(crate) fn carries(a: &Analysis) -> bool {
    matches!(&a.pmi, Some(Reading::Read(r)) if !r.records.is_empty())
}
pub(crate) fn strict(a: &Analysis, s: &Spec) -> bool {
    s.pmi == PmiMode::Strict && carries(a)
}
pub(super) fn authored(w: &Want) -> bool {
    w.id.starts_with("d:pmi:") || w.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
}
pub(super) fn wants(
    a: &Analysis,
    s: &Spec,
) -> (
    Vec<Want>,
    Vec<Problem>,
    BTreeMap<String, pmi_dimensions::Note>,
) {
    let mut result = if s.origin.is_empty() && s.pmi != PmiMode::Strict {
        super::wants(a)
    } else {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        let key = serde_json::to_vec(&(s.pmi, &s.origin)).unwrap();
        crate::planning::memo(a, "pmi-wants", key, || super::wants_uncached(a, s))
    };
    super::modelled_threads::apply(a, s, &mut result.0, &mut result.1);
    super::restate(a, s, &mut result.0, &mut result.1);
    super::blends::count_equal(s, &mut result.0);
    super::gussets::apply(s, &mut result.0);
    result
}

/// A semantic thread/knurl callout remains a source statement in strict mode; the engine's
/// surrounding drill sizes, depths and pattern counts do not become authored by association.
pub(super) fn strict_callouts(a: &Analysis, wants: &mut [Want]) {
    let manufacturing = crate::pmi_lowering::manufacturing(a);
    for w in wants
        .iter_mut()
        .filter(|w| authored(w) && matches!(w.shape, Shape::Callout { .. }))
    {
        let mut owners = BTreeSet::new();
        for m in &w.measurements {
            for o in &manufacturing.owned {
                if o.id == m.feature {
                    owners.insert(o.owner.clone());
                }
            }
        }
        let mut terms = Vec::new();
        for owner in owners {
            let (words, _) = manufacturing.terms(&owner);
            if !words.is_empty() && !terms.ends_with(&words) {
                terms.extend(words);
            }
        }
        if !terms.is_empty() {
            w.terms = terms;
            w.measurements.retain(|m| m.feature.starts_with("pmi:"));
            w.basis="source-authored: the part's semantic manufacturing callout, without generated sizes or counts".into();
        }
    }
}

fn origin(a: &Analysis, reference: &str, k: usize) -> Option<f64> {
    if let Some(index) = reference
        .strip_prefix("face:")
        .and_then(|word| word.parse::<usize>().ok())
    {
        let f = a.face_attachments.get(index)?;
        return (f.surface.as_deref() == Some("plane") && f.axis == Some(k))
            .then(|| f.sites.first().map(|p| p[k]))
            .flatten();
    }
    if reference == "min" || reference == "max" {
        return Some(a.envelope[usize::from(reference == "max")][k]);
    }
    // Feature origins must prove a planar face, not merely a cylindrical axis station.
    if let Some(f) = a.features.iter().find(|f| f.id == reference) {
        let mut planes: Vec<f64> = f
            .faces
            .iter()
            .filter_map(|&i| a.face_attachments.get(i))
            .filter(|f| f.surface.as_deref() == Some("plane") && f.axis == Some(k))
            .filter_map(|f| f.sites.first().map(|p| p[k]))
            .collect();
        planes.sort_by(f64::total_cmp);
        planes.dedup_by(|p, q| (*p - *q).abs() < 1e-6);
        return (planes.len() == 1).then(|| planes[0]);
    }
    match a.dimension_reference(reference).ok()? {
        crate::model::dimension_geometry::Reference::Plane { origin, normal, .. }
            if normal[k].abs() > 1. - 1e-8 =>
        {
            Some(origin[k])
        }
        _ => None,
    }
}

pub(crate) fn validate(a: &Analysis, s: &Spec) -> Result<(), Invalid> {
    for (axis, r) in &s.origin {
        let Some(k) = AXES.iter().position(|x| x == axis) else {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!("origin/{axis}: axis must be x, y or z."),
                choices: AXES.iter().map(|s| s.to_string()).collect(),
            });
        };
        if origin(a, r, k).is_none() {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "origin/{axis}: {r} must identify min, max or one plane normal to {axis}."
                ),
                choices: vec!["min".into(), "max".into()],
            });
        }
    }
    Ok(())
}

fn location_role(p: &str) -> bool {
    p.starts_with("location")
        || p.starts_with("seat_location.")
        || p.starts_with("step_position.length")
        || p.starts_with("step_height.length.h")
}
fn selected_origins(a: &Analysis, s: &Spec) -> ([Option<(f64, String)>; 3], Vec<Problem>) {
    let mut selected: [Option<(f64, String)>; 3] = [None, None, None];
    let mut problems = Vec::new();
    let mut unusable = false;
    if let Some(Reading::Read(r)) = &a.pmi {
        let mut datums: Vec<_> = r
            .records
            .iter()
            .filter(|r| r.category == Category::Datum)
            .collect();
        datums.sort_by(|p, q| p.label.cmp(&q.label).then(p.source_id.cmp(&q.source_id)));
        for d in datums {
            let usable = d.geometry.as_ref().and_then(|g| {
                if g.surface.as_deref() != Some("plane") {
                    return None;
                }
                let k = ["X", "Y", "Z"]
                    .iter()
                    .position(|x| Some(*x) == g.axis.as_deref())?;
                Some((k, g.sites.first()?[k]))
            });
            if let Some((k, v)) = usable {
                selected[k].get_or_insert((v, format!("datum {} ({})", d.label, d.source_id)));
            } else {
                unusable = true;
                problems.push(Problem { id:format!("p:datum_origin_unusable:{}",d.source_id),code:"datum_origin_unusable".into(),severity:"info".into(),message:format!("Datum {} cannot supply a principal planar origin; the box minimum remains the fallback.",d.label), refs:vec![d.source_id.clone()],..Default::default() });
            }
        }
    }
    if unusable {
        for (k, origin) in selected.iter_mut().enumerate() {
            origin.get_or_insert((
                a.envelope[0][k],
                "box minimum (no usable planar datum)".into(),
            ));
        }
    }
    for (k, axis) in AXES.iter().enumerate() {
        if let Some(r) = s.origin.get(*axis) {
            selected[k] = origin(a, r, k).map(|v| (v, format!("origin/{axis} {r}")));
        }
    }
    (selected, problems)
}

/// The source ids of the datums dimensions measure from: the planar datum selected for each axis
/// that [`origins`] applies and the Spec's `origin` does not override.
pub(crate) fn origin_datums(a: &Analysis, s: &Spec) -> BTreeSet<String> {
    if !carries(a) && s.origin.is_empty() {
        return BTreeSet::new();
    }
    let (selected, _) = selected_origins(a, s);
    let Some(Reading::Read(r)) = &a.pmi else {
        return BTreeSet::new();
    };
    selected
        .iter()
        .flatten()
        .filter_map(|(_, name)| {
            r.records
                .iter()
                .find(|d| {
                    d.category == Category::Datum
                        && *name == format!("datum {} ({})", d.label, d.source_id)
                })
                .map(|d| d.source_id.clone())
        })
        .collect()
}

pub(crate) fn origin_point(a: &Analysis, s: &Spec) -> [f64; 3] {
    let (selected, _) = selected_origins(a, s);
    std::array::from_fn(|k| selected[k].as_ref().map_or(a.envelope[0][k], |o| o.0))
}

pub(crate) fn origin_description(a: &Analysis, s: &Spec) -> String {
    let (selected, _) = selected_origins(a, s);
    AXES.iter()
        .enumerate()
        .map(|(k, axis)| {
            format!(
                "{axis}: {}",
                selected[k].as_ref().map_or("box minimum", |o| o.1.as_str())
            )
        })
        .collect::<Vec<_>>()
        .join(", ")
}

/// Source-only rooted location evidence, used when a schedule would otherwise add coordinates.
pub(crate) fn source_locations(a: &Analysis, p: &crate::model::Plan) -> BTreeSet<(String, usize)> {
    located_by(a, p, |d| {
        d.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
    })
    .into_keys()
    .collect()
}

pub(crate) fn retained_locations(
    a: &Analysis,
    p: &crate::model::Plan,
    replaced: &BTreeSet<String>,
) -> BTreeMap<(String, usize), Vec<String>> {
    located_by(a, p, |d| !replaced.contains(&d.id))
}

fn located_by(
    a: &Analysis,
    p: &crate::model::Plan,
    include: impl Fn(&crate::dims::PlannedDimension) -> bool,
) -> BTreeMap<(String, usize), Vec<String>> {
    if !carries(a) {
        return BTreeMap::new();
    }
    let marks = dimension_marks(a, p, include);
    let edges = location::edges(a, &marks);
    let roots = location::drawing_roots(a, &marks);
    a.features
        .iter()
        .flat_map(|f| {
            (0..3).filter_map(|k| {
                let node = location::node_feature(a, &f.id, k)?;
                let at = location::station(a, &f.id, k)?;
                location::proof(a, &edges, &roots[k], k, at, Some(&node)).map(|path| {
                    (
                        (f.id.clone(), k),
                        path.into_iter().map(|i| edges[i].owner.clone()).collect(),
                    )
                })
            })
        })
        .collect()
}

pub(super) fn origins(a: &Analysis, s: &Spec, wants: &mut [Want]) -> Vec<Problem> {
    if !carries(a) && s.origin.is_empty() {
        return Vec::new();
    }
    let (selected, mut problems) = selected_origins(a, s);
    for w in wants {
        if let Shape::Linear {
            a: p,
            b: q,
            axis: k,
            ..
        } = &mut w.shape
            && w.measurements
                .first()
                .is_some_and(|m| location_role(&m.parameter))
            && let Some((v, name)) = &selected[*k]
        {
            // These candidates are always oriented from the original origin to the feature.
            p[*k] = *v;
            w.value = (q[*k] - v).abs();
            w.terms = vec![fmt(w.value)];
            for m in &mut w.measurements {
                if location_role(&m.parameter) {
                    m.value = w.value;
                }
            }
            w.basis += &format!("; measured from {name}");
            if w.value < 1e-9 {
                problems.push(Problem{id:format!("p:location_on_datum:{}",w.id),code:"location_on_datum".into(),severity:"info".into(),
                        message:"A feature lies on the selected datum; its location has no nonzero distance to draw.".into(),
                        subjects:w.measurements.iter().filter(|m|a.features.iter().any(|f|f.id==m.feature)).map(|m|m.feature.clone()).collect(),..Default::default()});
            }
        }
    }
    problems
}

pub(super) fn on_origin(w: &Want) -> bool {
    matches!(&w.shape,Shape::Linear{a,b,axis,..} if (a[*axis]-b[*axis]).abs()<1e-9)
        && w.measurements.iter().any(|m| location_role(&m.parameter))
}

fn spans(a: &Analysis, w: &Want) -> Vec<Edge> {
    let mark = crate::lint::Mark {
        name: w.id.clone(),
        view: None,
        text: crate::callouts::label(&w.terms),
        claims: w
            .measurements
            .iter()
            .map(|m| crate::output::Claim {
                feature: m.feature.clone(),
                parameter: m.parameter.clone(),
            })
            .collect(),
    };
    location::identify(a, &mark, raw_spans(a, w))
}
fn raw_spans(a: &Analysis, w: &Want) -> Vec<Edge> {
    let Shape::Linear {
        a: p,
        b: q,
        axis: k,
        ..
    } = w.shape
    else {
        return vec![];
    };
    if w.measurements
        .iter()
        .any(|m| m.parameter.contains("pitch.length"))
    {
        let mut out = Vec::new();
        for m in &w.measurements {
            out.extend(
                crate::lint::schemes::supports(
                    a,
                    &m.feature,
                    &m.parameter,
                    &crate::callouts::label(&w.terms),
                )
                .into_iter()
                .map(|s| Edge {
                    axis: s.axis,
                    a: s.lo,
                    b: s.hi,
                    owner: w.id.clone(),
                    a_feature: None,
                    b_feature: None,
                }),
            );
        }
        if !out.is_empty() {
            return out;
        }
    }
    vec![Edge {
        axis: k,
        a: p[k],
        b: q[k],
        owner: w.id.clone(),
        a_feature: None,
        b_feature: None,
    }]
}

pub(super) fn cover(
    a: &Analysis,
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
) {
    if !carries(a) {
        return;
    }
    // Recovery revisits the same world-space constraints at several paper scales.
    // Include every input (view choices, labels and authored tolerances included)
    // so only identical precedence decisions share this pass-local result.
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    let key = serde_json::to_vec(&(&*kept, &*given)).expect("precedence inputs serialize");
    let (items, terms, covered) = crate::planning::memo(a, "pmi-precedence", key, || {
        let mut items = kept.clone();
        let mut terms = given.clone();
        let mut covered = Vec::new();
        cover_uncached(a, &mut items, &mut terms, &mut covered);
        (items, terms, covered)
    });
    *kept = items;
    *given = terms;
    omitted.extend(covered);
}

fn cover_uncached(
    a: &Analysis,
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
) {
    if !carries(a) {
        return;
    }
    let marks: Vec<_> = kept
        .iter()
        .map(|(w, ..)| crate::lint::Mark {
            name: w.id.clone(),
            view: None,
            text: crate::callouts::label(&w.terms),
            claims: w
                .measurements
                .iter()
                .map(|m| crate::output::Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect();
    let roots = location::drawing_roots(a, &marks);
    let mut edges: Vec<_> = kept
        .iter()
        .filter(|(w, _, view, _)| authored(w) && view.is_some())
        .flat_map(|(w, ..)| spans(a, w))
        .collect();
    let mut gone = BTreeSet::new();
    // Sizes are owned by their physical feature. A whole-part extent is the size
    // between its two support planes; feature sizes additionally require shared faces.
    for i in 0..kept.len() {
        let (w, default, ..) = &kept[i];
        if authored(w)
            || !*default
            || !given[i].is_empty()
            || (!w.measurements.iter().any(|m| m.feature == "envelope")
                && w.measurements.iter().any(|m| {
                    location_role(&m.parameter)
                        || m.parameter.contains("pitch.length")
                            && m.parameter != "gusset_pitch.length"
                }))
        {
            continue;
        }
        let owner =
            kept.iter().enumerate().find_map(|(j, (p, ..))| {
                if !authored(p) || i == j || kept[j].2.is_none() {
                    return None;
                }
                let same_feature =
                    w.measurements.iter().any(|m| m.feature == "envelope")
                        || match &a.pmi {
                            Some(Reading::Read(report)) => report.records.iter().any(|r| {
                                r.category == Category::Dimension
                                    && report.identities().id(&r.source_id).ok().is_some_and(|id| {
                                        p.measurements.iter().any(|m| m.feature == id)
                                    })
                                    && !r.faces.is_empty()
                                    && w.measurements.iter().any(|m| {
                                        a.features.iter().find(|f| f.id == m.feature).is_some_and(
                                            |f| r.faces.iter().all(|i| f.faces.contains(i)),
                                        )
                                    })
                            }),
                            _ => false,
                        };
                if !same_feature {
                    return None;
                }
                let ws = spans(a, w);
                let ps = spans(a, p);
                (!ws.is_empty()
                    && ws.iter().all(|s| {
                        ps.iter().any(|q| {
                            s.axis == q.axis
                                && ((s.a - q.a).abs() < 1e-6 && (s.b - q.b).abs() < 1e-6
                                    || (s.a - q.b).abs() < 1e-6 && (s.b - q.a).abs() < 1e-6)
                        })
                    }))
                .then_some(j)
            });
        // An authored datum baseline controls both ends of a local engine size.
        // Retaining that size would close a tolerance chain (for example the end
        // thickness beside two baseline locations). Require a connected path of
        // actual source spans, rather than two independently rooted stations.
        let same_span = owner.is_some();
        let owner = owner.or_else(|| {
            let ws = spans(a, w);
            if ws.len() != 1 {
                return None;
            }
            let span = &ws[0];
            if span.a_feature.is_some() || span.b_feature.is_some() {
                return None;
            }
            let path =
                location::route(&edges, &[span.a], span.axis, span.b, None, &BTreeSet::new())?;
            let Some(Reading::Read(report)) = &a.pmi else {
                return None;
            };
            let ids = report.identities();
            path.into_iter().find_map(|e| {
                let j = kept.iter().position(|(p, ..)| p.id == edges[e].owner)?;
                report
                    .records
                    .iter()
                    .any(|r| {
                        crate::pmi::datum_referenced(r)
                            && ids.id(&r.source_id).ok().is_some_and(|id| {
                                kept[j].0.measurements.iter().any(|m| m.feature == id)
                            })
                    })
                    .then_some(j)
            })
        });
        if let Some(j) = owner {
            let by = kept[j].0.id.clone();
            let claims = kept[i]
                .0
                .measurements
                .iter()
                .filter(|m| {
                    !m.parameter.contains("pitch.length") || m.parameter == "gusset_pitch.length"
                })
                .cloned()
                .collect::<Vec<_>>();
            omitted.push(Omitted {
                id: kept[i].0.id.clone(),
                label: crate::callouts::label(&kept[i].0.terms),
                basis: if same_span {
                    format!("conveyed by {by}: the part's PMI states this feature's size")
                } else {
                    format!("conveyed by {by}: authored datum baselines control both support planes; this local engine size would close a tolerance chain")
                },
                state: State::Covered { by },
            });
            kept[j].0.measurements.extend(claims);
            gone.insert(i);
        }
    }
    // Retirable size candidates cannot seed coverage of their own baseline rungs.
    // Establish support-plane locations before feature axes, and pattern links last.
    let mut order: Vec<_> = (0..kept.len()).collect();
    order.sort_by_key(|&i| {
        let w = &kept[i].0;
        if w.measurements.iter().any(|m| {
            m.parameter.starts_with("step_position") || m.parameter.starts_with("step_height")
        }) {
            0
        } else if w
            .measurements
            .iter()
            .any(|m| m.parameter.contains("pitch.length"))
        {
            1
        } else {
            2
        }
    });
    for i in order {
        let (w, default, ..) = kept[i].clone();
        if gone.contains(&i) || authored(&w) || !default || !given[i].is_empty() {
            continue;
        }
        // Gusset pitch is a printed group size, never an inferred location chain.
        if w.measurements
            .iter()
            .any(|m| m.parameter == "gusset_pitch.length")
        {
            continue;
        }
        let is_location = w.measurements.iter().any(|m| location_role(&m.parameter))
            || w.basis.contains("first rung of the profile's baseline");
        let pitch = w
            .measurements
            .iter()
            .any(|m| m.parameter.contains("pitch.length"));
        if !is_location && !pitch {
            continue;
        }
        let spans = spans(a, &w);
        if spans.is_empty() {
            continue;
        }
        let mut owners = BTreeSet::new();
        let mut uncontrolled = Vec::new();
        for s in &spans {
            let first = location::proof(
                a,
                &edges,
                &roots[s.axis],
                s.axis,
                s.b,
                s.b_feature.as_deref(),
            );
            let second = if is_location {
                Some(vec![])
            } else {
                location::proof(
                    a,
                    &edges,
                    &roots[s.axis],
                    s.axis,
                    s.a,
                    s.a_feature.as_deref(),
                )
            };
            if let (Some(p), Some(q)) = (first, second) {
                if p.is_empty()
                    && let Some(feature) = &s.b_feature
                    && location::datum_axis_owner(a, feature, s.axis).is_some()
                    && let Some(witness) = edges.iter().find(|e| {
                        e.axis == s.axis
                            && (e.a.min(e.b) - s.a.min(s.b)).abs() < 1e-6
                            && (e.a.max(e.b) - s.a.max(s.b)).abs() < 1e-6
                    })
                {
                    // The datum axis already roots this coaxial element. Keep its
                    // numeric claim on a live source witness of that same interval;
                    // an uncited datum symbol is not a drawn measurement owner.
                    owners.insert(witness.owner.clone());
                }
                for j in p.into_iter().chain(q) {
                    owners.insert(edges[j].owner.clone());
                }
            } else {
                uncontrolled.push(s.clone());
            }
        }
        if uncontrolled.is_empty() && !owners.is_empty() {
            // Invariant: The preceding condition requires owners to be nonempty.
            let owner = owners.iter().next().unwrap().clone();
            omitted.push(Omitted {
                id: w.id.clone(),
                label: crate::callouts::label(&w.terms),
                basis: format!(
                    "conveyed by {owner}: the part's PMI rooted location network locates this element through {}",
                    owners.into_iter().collect::<Vec<_>>().join(", ")
                ),
                state: State::Covered { by: owner.clone() },
            });
            let claims: Vec<_> = kept[i]
                .0
                .measurements
                .iter()
                .filter(|m| !m.parameter.contains("pitch.length"))
                .cloned()
                .collect();
            if let Some(j) = kept.iter().position(|(w, ..)| w.id == owner) {
                kept[j].0.measurements.extend(claims);
            }
            gone.insert(i);
        } else if is_location
            && !uncontrolled.is_empty()
            && !owners.is_empty()
            && uncontrolled.len() < spans.len()
            && uncontrolled.iter().all(|e| e.b_feature.is_some())
        {
            // One shared engine ordinate can name several coincident feature axes. Keep
            // only its unlocated members; a PMI statement on one axis cannot locate another.
            let members: BTreeSet<_> = uncontrolled
                .iter()
                .filter_map(|e| e.b_feature.clone())
                .collect();
            let role = w
                .measurements
                .iter()
                .find(|m| location_role(&m.parameter))
                // Invariant: is_location was set from a measurement with this same location_role predicate.
                .unwrap()
                .parameter
                .clone();
            let target = &mut kept[i].0;
            target.measurements.retain(|m| !location_role(&m.parameter));
            target
                .measurements
                .extend(members.iter().map(|id| Measurement {
                    feature: id.clone(),
                    parameter: role.clone(),
                    value: w.value,
                }));
            if let Some(point) = members
                .iter()
                .find_map(|id| a.scheme_station(id, uncontrolled[0].axis).ok())
                && let Shape::Linear {
                    a: p, b: q, axis, ..
                } = &mut target.shape
            {
                let origin = p[*axis];
                *p = point;
                *q = point;
                p[*axis] = origin;
            }
            target.basis += "; only the member axes not already located by PMI";
            edges.extend(self::spans(a, target));
        } else {
            edges.extend(spans);
        }
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

pub(crate) fn ignore(
    a: &Analysis,
    s: &Spec,
    planner: fn(&Analysis, &Spec) -> Result<crate::model::Plan, Invalid>,
) -> Result<crate::model::Plan, Invalid> {
    let count = match &a.pmi {
        Some(Reading::Read(r)) => r.records.len(),
        _ => 0,
    };
    let mut plain = a.clone();
    plain.pmi = None;
    let mut intent = s.clone();
    intent.pmi = PmiMode::First;
    let mut p = planner(&plain, &intent)?;
    p.spec.pmi = PmiMode::Ignore;
    p.analysis_sha256 = Some({
        use sha2::{Digest, Sha256};
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Sha256::digest(serde_json::to_vec(a).unwrap())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    });
    p.problems.push(Problem {
        id: "p:pmi_ignored:part".into(),
        code: "pmi_ignored".into(),
        severity: "info".into(),
        message: format!("The part carries {count} PMI items that were not drawn (pmi ignore)."),
        refs: vec!["part".into()],
        ..Default::default()
    });
    // Keep the same feature inventory, but hidden source datums are not drawing origins.
    // Explicit Spec controls remain part of the plain drawing and its origin frame.
    let effective = crate::spec_gdt::effective(&plain, s)?;
    audit(effective.as_ref(), s, &mut p);
    Ok(p)
}

pub(crate) fn audit(a: &Analysis, s: &Spec, p: &mut crate::model::Plan) {
    if PLAN_DEPTH.get() > 1 || (!location::has_station_requirements(a) && s.origin.is_empty()) {
        return;
    }
    crate::planning::with(a, || audit_inner(a, s, p));
}

fn dimension_marks(
    a: &Analysis,
    p: &crate::model::Plan,
    include: impl Fn(&PlannedDimension) -> bool,
) -> Vec<crate::lint::Mark> {
    let mut marks: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some() && !d.reference && include(d))
        .map(|d| crate::lint::Mark {
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: d
                .measurements
                .iter()
                .map(|m| crate::output::Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect();
    for (mark, d) in marks.iter_mut().zip(
        p.dimensions
            .iter()
            .filter(|d| d.placed.is_some() && !d.reference && include(d)),
    ) {
        if d.kind != "linear" {
            continue;
        }
        let Some(view) = p.views.iter().find(|v| v.id == d.view) else {
            continue;
        };
        let at = if view.detail.is_some() {
            Frame::new(a, view).page([0, 1, 2].map(|k| 0.5 * (a.envelope[0][k] + a.envelope[1][k])))
        } else {
            view.at
        };
        let frame = crate::output::DrawnFrame {
            direction: view.direction,
            up: view.up,
            at,
            scale: view.scale,
            half_axis: None,
        };
        mark.claims
            .retain(|c| !c.feature.starts_with("station_span:") && c.feature != "station_ink");
        mark.claims.push(crate::output::Claim {
            feature: "station_ink".into(),
            parameter: "witnesses".into(),
        });
        // Invariant: The preceding dimension iterator selects placed dimensions.
        if let Some((axis, q, r)) = location::ink_span(a, &frame, &d.placed.as_ref().unwrap().lines)
        {
            mark.claims.push(crate::output::Claim {
                feature: location::span_claim(axis, q, r),
                parameter: "station.distance".into(),
            });
        }
    }
    for (axis, reference) in &p.spec.origin {
        // Invariant: ink_span returns only the fixed x, y or z axis names.
        let k = AXES.iter().position(|v| v == axis).unwrap();
        marks.push(crate::lint::Mark {
            name: format!("origin/{axis}"),
            view: None,
            text: String::new(),
            claims: vec![crate::output::Claim {
                feature: format!("station_origin:{k}|{reference}"),
                parameter: "origin".into(),
            }],
        });
    }
    // A grouped slot's centre conveyed by another feature's location line is located by it.
    crate::lint::slots::conveyed_locations(a, &mut marks);
    // So is a boss's centre on a hole's or pattern's location line.
    location::conveyed_boss_locations(a, &mut marks);
    marks
}

fn audit_inner(a: &Analysis, s: &Spec, p: &mut crate::model::Plan) {
    let mut marks = dimension_marks(a, p, |_| true);
    // A placed coordinate schedule is a drawn locating network too. Verify its cell value
    // in its declared datum frame, then carry that physical edge to the same shared checker.
    for table in p
        .hole_table
        .iter()
        .chain(&p.hole_tables)
        .filter(|t| t.blocks.iter().any(|b| b.placed.is_some()))
    {
        let origin = table.origin.unwrap_or(a.envelope[0]);
        for row in &table.rows {
            for m in &row.measurements {
                let Some(k) = AXES
                    .iter()
                    .position(|axis| m.parameter == format!("location.length.{axis}"))
                else {
                    continue;
                };
                let Some(at) = location::station(a, &m.feature, k) else {
                    continue;
                };
                let value = at - origin[k];
                if !row.cells.iter().any(|s| {
                    s.split_whitespace()
                        .next()
                        .and_then(|n| n.parse::<f64>().ok())
                        .is_some_and(|n| n.signum() == value.signum())
                        && crate::lint::holes::states(s, value.abs())
                }) {
                    continue;
                }
                let mut claims = vec![
                    crate::output::Claim {
                        feature: m.feature.clone(),
                        parameter: m.parameter.clone(),
                    },
                    crate::output::Claim {
                        feature: "station_ink".into(),
                        parameter: "coordinate".into(),
                    },
                    crate::output::Claim {
                        feature: location::span_claim(k, origin[k], at),
                        parameter: "station.distance".into(),
                    },
                ];
                let reference = s.origin.get(AXES[k]).cloned().or_else(|| {
                    a.face_attachments
                        .iter()
                        .enumerate()
                        .find(|(_, f)| {
                            f.surface.as_deref() == Some("plane")
                                && f.axis == Some(k)
                                && f.sites
                                    .first()
                                    .is_some_and(|p| (p[k] - origin[k]).abs() < 1e-6)
                        })
                        .map(|(i, _)| format!("face:{i}"))
                });
                if let Some(reference) = reference {
                    claims.push(crate::output::Claim {
                        feature: format!("station_origin:{k}|{reference}"),
                        parameter: "origin".into(),
                    });
                }
                marks.push(crate::lint::Mark {
                    name: format!("{}/{}/{}", table.id, row.tag, AXES[k]),
                    view: table.view.clone(),
                    text: format!("{value}"),
                    claims,
                });
            }
        }
    }
    p.problems.retain(|p| {
        !matches!(
            p.code.as_str(),
            "feature_not_located" | "feature_located_twice" | "feature_not_sized"
        )
    });
    let notches = crate::notches::requirements(a);
    for issue in location::coverage(a, &marks) {
        p.problems.push(Problem {
            id: issue.id.replacen("l:", "p:", 1),
            code: issue.code,
            severity: "warning".into(),
            message: {
                let mut message = issue.message;
                for d in &p.dimensions {
                    message = message.replace(&d.id, &format!("dimension {}", d.label));
                }
                for f in &issue.subjects {
                    message = message.replace(f, &crate::wording::feature_id(a, f));
                }
                message
            },
            subjects: issue
                .subjects
                .into_iter()
                .flat_map(|id| {
                    if let Some(n) = notches.iter().find(|n| n.id == id) {
                        n.faces
                            .iter()
                            .map(|i| format!("face:{i}"))
                            .collect::<Vec<_>>()
                    } else {
                        vec![id]
                    }
                })
                .chain(issue.related)
                .collect(),
            ..Default::default()
        });
    }
    if strict(a, s) {
        for issue in location::sizes(a, &marks) {
            p.problems.push(Problem {
                id: issue.id.replacen("l:", "p:", 1),
                code: issue.code,
                severity: "warning".into(),
                message: {
                    let mut message = issue.message;
                    for d in &p.dimensions {
                        message = message.replace(&d.id, &format!("dimension {}", d.label));
                    }
                    for f in &issue.subjects {
                        message = message.replace(f, &crate::wording::feature_id(a, f));
                    }
                    message
                },
                subjects: issue.subjects,
                ..Default::default()
            });
        }
    }
    for problem in p.problems.iter_mut().filter(|p| {
        matches!(
            p.code.as_str(),
            "feature_not_located" | "feature_located_twice" | "feature_not_sized"
        )
    }) {
        for table in p.hole_table.iter().chain(&p.hole_tables) {
            for row in &table.rows {
                for axis in AXES {
                    let cell = format!("{}/{}/{axis}", table.id, row.tag);
                    problem.message = problem
                        .message
                        .replace(&cell, &format!("table coordinate {} {axis}", row.tag));
                }
            }
            for id in &mut problem.subjects {
                if id.starts_with(&format!("{}/", table.id)) {
                    *id = table.id.clone();
                }
            }
        }
        let mut seen = BTreeSet::new();
        problem.subjects.retain(|s| seen.insert(s.clone()));
    }
    p.status = crate::completeness::status(&p.problems);
}
