//! Blend radii: equal displayed values share exact occurrence credit. A frequent radius is
//! stated by one general note; other radii are exceptions, called out where their arc reads.
use std::collections::BTreeMap;

use super::{Context, Measurement, Planner, Problem, Shape, Want, end_on, fmt, want_id};
use crate::bridge::Feature;
use crate::bridge::holes::{frame, principal};
use crate::model::Analysis;
use crate::notes::NoteSpec;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["blend"],
    wants,
};
const GENERAL_COUNT: usize = 5;
pub(crate) use crate::lint::blends::{RADIUS_TABLE, RELIEF_NOTE};
type ViewSites<'a> = BTreeMap<(&'static str, usize), Vec<([f64; 3], &'a Feature)>>;

fn drawing_order(f: &Feature) -> &str {
    f.legacy_id.as_deref().unwrap_or(&f.id)
}

fn groups(a: &Analysis) -> Vec<Vec<&Feature>> {
    let pocket_corners = crate::lint::pockets::pattern_corner_blends(a);
    let mut groups = BTreeMap::<String, Vec<&Feature>>::new();
    for f in a.features.iter().filter(|f| {
        f.kind == "blend"
            && !crate::lint::blends::is_relief(a, f)
            && !pocket_corners.contains(&f.id)
    }) {
        if let Some(r) = radius(f) {
            groups.entry(fmt(r)).or_default().push(f);
        }
    }
    let mut out: Vec<_> = groups.into_values().collect();
    for group in &mut out {
        group.sort_by(|a, b| drawing_order(a).cmp(drawing_order(b)));
    }
    out.sort_by(|a, b| {
        radius(a[0])
            // Invariant: groups contains only features with a successfully read radius.
            .unwrap()
            // Invariant: groups contains only features with a successfully read radius.
            .total_cmp(&radius(b[0]).unwrap())
            .then(drawing_order(a[0]).cmp(drawing_order(b[0])))
    });
    out
}
fn radius(f: &Feature) -> Option<f64> {
    f.parameters
        .get("radius")?
        .as_f64()
        .filter(|r| r.is_finite() && *r > 0.0)
}
fn general(groups: &[Vec<&Feature>]) -> Option<usize> {
    groups
        .iter()
        .enumerate()
        .filter(|(_, g)| {
            g.len() >= 2 && groups.iter().map(Vec::len).sum::<usize>() >= GENERAL_COUNT
        })
        .max_by_key(|(i, g)| (g.len(), std::cmp::Reverse(*i)))
        .map(|(i, _)| i)
}
fn measurements(group: &[&Feature]) -> Vec<Measurement> {
    group
        .iter()
        .map(|f| Measurement {
            feature: f.id.clone(),
            parameter: "blend.radius".into(),
            // Invariant: Features entered the group only after radius returned Some.
            value: radius(f).unwrap(),
        })
        .collect()
}

fn sites_by_view<'a>(a: &Analysis, group: &[&'a Feature]) -> ViewSites<'a> {
    let mut by_view = ViewSites::new();
    for &f in group {
        let Some(path) = f.parameters.get("path") else {
            continue;
        };
        let Some(dir) = crate::lint::parameters::direction(
            &frame(a),
            path.get("direction").or(path.get("normal")),
        ) else {
            continue;
        };
        let Some(axis) = principal(dir) else { continue };
        if let Some(sites) = a.blend_sites.get(&f.id) {
            for &site in sites {
                let views = if path.get("normal").is_some() {
                    super::envelope::profile_views(axis)
                } else {
                    vec![end_on(axis)]
                };
                for view in views {
                    by_view.entry((view, axis)).or_default().push((site, f));
                }
            }
        }
    }
    by_view
}

/// Automatic notes carry explicit measurement identities; prose authored in Spec never does.
pub(crate) fn notes(a: &Analysis) -> Vec<(NoteSpec, Vec<Measurement>)> {
    let groups = groups(a);
    let mut out = Vec::new();
    if let Some(i) = general(&groups) {
        let group = &groups[i];
        out.push((
            NoteSpec {
                id: format!("n:{}/blend.radius", group[0].id),
                // Invariant: Groups are nonempty and contain only features with a read radius.
                text: format!("ALL UNSPECIFIED RADII R{}", fmt(radius(group[0]).unwrap())),
            },
            measurements(group),
        ));
    }
    if !a.radius_reliefs.is_empty() {
        out.push((
            NoteSpec {
                id: RELIEF_NOTE.into(),
                text: "SHALLOW RELIEF PROFILES PER MODEL".into(),
            },
            Vec::new(),
        ));
    }
    out
}

/// Sampling along a cylinder's axis produces the same attachment in its end view.
/// Keep the first physical point for each projection, without repeating the leader search.
fn projected_sites(view: &str, sites: impl Iterator<Item = [f64; 3]>) -> Vec<[f64; 3]> {
    let toward = match view {
        "front" => 1,
        "plan" => 2,
        "side" => 0,
        // Invariant: The match receives only the blend/fillet kinds admitted above.
        _ => unreachable!(),
    };
    let mut out: Vec<[f64; 3]> = Vec::new();
    for p in sites {
        if !out
            .iter()
            .any(|q| (0..3).all(|k| k == toward || (p[k] - q[k]).abs() < 1e-9))
        {
            out.push(p);
        }
    }
    out
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let a = cx.analysis;
    let groups = groups(a);
    let general = general(&groups);
    let mut wants = Vec::new();
    let mut problems = Vec::new();
    for f in a
        .features
        .iter()
        .filter(|f| f.kind == "blend" && radius(f).is_none())
    {
        problems.push(problem(f, "its record gives no positive finite radius"));
    }
    for (i, group) in groups.iter().enumerate() {
        if Some(i) == general {
            continue;
        }
        let by_view = sites_by_view(a, group);
        let Some((&(view, _axis), sites)) = by_view
            .iter()
            .min_by_key(|(key, sites)| (std::cmp::Reverse(sites.len()), **key))
        else {
            for f in group {
                problems.push(problem(
                    f,
                    "no principal view has a proven attachment on its trimmed blend surface",
                ));
            }
            continue;
        };
        // Invariant: Groups are nonempty and contain only features with a read radius.
        let r = radius(group[0]).unwrap();
        let terms = if group.len() > 1 {
            vec![format!("{}×", group.len()), format!("R{}", fmt(r))]
        } else {
            vec![format!("R{}", fmt(r))]
        };
        wants.push(Want {
            id: want_id(&group[0].id, "blend.radius"), views: vec![view], value: r,
            terms, measurements: measurements(group),
            basis: "equal displayed blend radii are stated once, with an exact measurement claim for every occurrence and a leader on a proven trimmed surface".into(),
            shape: Shape::Callout { arcs: Vec::new(),  sites: projected_sites(view, sites.iter().map(|(p, _)| *p)).into_iter().map(|p| (p, 0.0)).collect() }, turning: None,
        });
    }
    (wants, problems)
}
/// A radius exception on an oblique axis is seen as an arc in no principal view, so it can have
/// no leader of its own. When a fillet callout already states the same radius, it is counted
/// there with its exact claim, as draftwright counts members that read in another view, so the
/// leader is laid out with its final count and the exception is stated wherever it is drawn.
pub(super) fn count_oblique(a: &Analysis, wants: &mut [Want], problems: &mut Vec<Problem>) {
    let oblique = |f: &Feature| {
        f.parameters
            .get("path")
            .and_then(|p| {
                crate::lint::parameters::direction(
                    &frame(a),
                    p.get("direction").or(p.get("normal")),
                )
            })
            .is_some_and(|d| principal(d).is_none())
    };
    let groups = groups(a);
    let general = general(&groups);
    for (i, group) in groups.iter().enumerate() {
        if Some(i) == general || !group.iter().all(|f| oblique(f)) {
            continue;
        }
        // Invariant: Groups are nonempty and contain only features with a read radius.
        let r = radius(group[0]).unwrap();
        let Some(w) = wants.iter_mut().find(|w| {
            matches!(w.shape, Shape::Callout { .. })
                && !w.measurements.is_empty()
                && w.measurements.iter().all(|m| {
                    matches!(
                        m.parameter.as_str(),
                        "fillet.radius" | "cylindrical_profile.radius"
                    )
                })
                && fmt(w.value) == fmt(r)
        }) else {
            continue;
        };
        w.measurements.extend(measurements(group));
        w.terms = vec![
            format!("{}×", w.measurements.len()),
            format!("R{}", fmt(w.value)),
        ];
        w.basis += &format!(
            "; {} equal blend{} on an oblique axis, seen as an arc in no principal view, {} counted here",
            group.len(),
            if group.len() == 1 { "" } else { "s" },
            if group.len() == 1 { "is" } else { "are" },
        );
        problems.retain(|p| {
            !(p.code == "feature_not_dimensioned"
                && group.iter().any(|f| p.subjects == [f.id.clone()]))
        });
    }
}

/// [`consolidate`] joins an equal blend callout to the fillet callout after layout. Give the
/// fillet its final count first, so its leader is placed with the label it will carry rather
/// than relabelled after its space was judged; a longer label could then run past its view's
/// area. Only where consolidation will apply: neither callout overridden, and one fillet
/// callout of that radius.
pub(super) fn count_equal(spec: &crate::model::Spec, wants: &mut [Want]) {
    let callout = |w: &Want, p: &str| {
        !overridden(spec, spec, &w.id)
            && matches!(w.shape, Shape::Callout { .. })
            && !w.measurements.is_empty()
            && w.measurements.iter().all(|m| m.parameter == p)
    };
    let mut counts = BTreeMap::<String, usize>::new();
    for w in wants.iter().filter(|w| callout(w, "blend.radius")) {
        *counts.entry(fmt(w.value)).or_default() += w.measurements.len();
    }
    for (r, blends) in counts {
        let fillets: Vec<_> = (0..wants.len())
            .filter(|&j| {
                matches!(wants[j].shape, Shape::Callout { .. })
                    && wants[j].measurements.iter().any(|m| {
                        matches!(
                            m.parameter.as_str(),
                            "fillet.radius" | "cylindrical_profile.radius"
                        )
                    })
                    && fmt(wants[j].value) == r
            })
            .collect();
        if let [j] = fillets[..]
            && !overridden(spec, spec, &wants[j].id)
            && wants[j].measurements.iter().all(|m| {
                matches!(
                    m.parameter.as_str(),
                    "fillet.radius" | "blend.radius" | "cylindrical_profile.radius"
                )
            })
        {
            let w = &mut wants[j];
            w.terms = vec![
                format!("{}×", w.measurements.len() + blends),
                format!("R{}", fmt(w.value)),
            ];
        }
    }
}

fn problem(f: &Feature, why: &str) -> Problem {
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

/// All possible automatic radius-note identities, independent of layout or omission.
pub(crate) fn note_ids(a: &Analysis) -> Vec<String> {
    let mut ids: Vec<_> = groups(a)
        .iter()
        .map(|g| format!("n:{}/blend.radius", g[0].id))
        .collect();
    if !a.radius_reliefs.is_empty() {
        ids.push(RELIEF_NOTE.into());
    }
    ids
}

/// A default cannot be laid out until every radius exception has a callout.
pub(crate) fn exceptions_unstated(
    a: &Analysis,
    dimensions: &[super::PlannedDimension],
    notes: &[(NoteSpec, Vec<Measurement>)],
) -> bool {
    let Some(value) = notes
        .first()
        .and_then(|(_, ms)| ms.first())
        .map(|m| m.value)
    else {
        return false;
    };
    a.features
        .iter()
        .filter(|f| {
            matches!(f.kind.as_str(), "blend" | "fillet" | "cylindrical_profile") && !crate::lint::blends::is_relief(a, f)
        })
        .any(|f| {
            radius(f).is_none_or(|r| fmt(r) != fmt(value))
                && !dimensions
                    .iter()
                    .any(|d| (d.placed.is_some() || matches!(&d.state, crate::items::State::Covered { by } if by == RADIUS_TABLE)) && d.measurements.iter().any(|m| m.feature == f.id))
        })
}

fn leader_problem(code: &str) -> bool {
    matches!(
        code,
        "annotation_ink_overlap"
            | "annotation_overlap"
            | "annotation_over_view"
            | "annotation_line_over_view"
            | "annotation_leader_crossing"
            | "annotation_outside_area"
            | "annotation_off_sheet"
            | "leader_through_feature"
            | "annotation_leader_long"
            | "annotation_leader_reenters"
    )
}

/// Consolidation removes only the redundant leader's diagnostics. Required exceptions
/// retain Policy B: routing cost is reported against their drawn ink, never withdrawn.
pub(crate) fn clear_covered_findings(
    dimensions: &[super::PlannedDimension],
    problems: &mut Vec<Problem>,
) {
    for d in dimensions.iter().filter(|d| {
        matches!(d.state, crate::items::State::Covered { .. })
            && d.measurements.iter().any(|m| m.parameter == "blend.radius")
    }) {
        problems.retain(|p| !p.subjects.contains(&d.id) || !leader_problem(&p.code));
    }
}

fn overridden(spec: &crate::model::Spec, given: &crate::model::Spec, id: &str) -> bool {
    // A bare pin gets a computed carrier in the effective Spec. Only authored placement
    // choices override grouping; inactive entries in the original Spec have no authority.
    spec.precision.contains_key(id)
        || spec.dimensions.get(id).is_some_and(|s| {
            s.omit
            || s.text.is_some()
            || s.style.is_some()
            || s.tolerance.is_some()
            || s.view.is_some()
            || given
                .dimensions
                .get(id)
                .and_then(|d| d.place.as_ref())
                .is_some_and(|p| {
                    p.view.is_some()
                        || p.side.is_some()
                        || p.lane.is_some()
                        || p.along.is_some()
                })
            // An offset at the automatic bend is planned as a bare pin (#12).
            || s.place.as_ref().is_some_and(|p| p.offset.is_some())
        })
}

/// The recogniser's fillet/blend distinction is not a second radius requirement on paper.
/// Keep the established fillet leader and give it the equal blend group's exact claims.
pub(crate) fn consolidate(
    dimensions: &mut [super::PlannedDimension],
    spec: &crate::model::Spec,
    given: &crate::model::Spec,
) {
    for i in 0..dimensions.len() {
        if dimensions[i].measurements.is_empty()
            || !dimensions[i]
                .measurements
                .iter()
                .all(|m| m.parameter == "blend.radius")
            || overridden(spec, given, &dimensions[i].id)
        {
            continue;
        }
        let Some(j) = dimensions.iter().position(|d| {
            d.placed.is_some()
                && d.measurements.iter().all(|m| {
                    matches!(
                        m.parameter.as_str(),
                        "fillet.radius" | "blend.radius" | "cylindrical_profile.radius"
                    )
                })
                && d.measurements.iter().any(|m| {
                    matches!(
                        m.parameter.as_str(),
                        "fillet.radius" | "cylindrical_profile.radius"
                    )
                })
                && fmt(d.value) == fmt(dimensions[i].value)
                && !overridden(spec, given, &d.id)
        }) else {
            continue;
        };
        let ms = dimensions[i].measurements.clone();
        dimensions[j].measurements.extend(ms);
        let n = dimensions[j].measurements.len();
        let was = dimensions[j].terms.clone();
        dimensions[j].terms = vec![format!("{n}×"), format!("R{}", fmt(dimensions[j].value))];
        dimensions[j].label = dimensions[j].terms.join(" ");
        // Invariant: The selected dimension indices were filtered to placed dimensions.
        let placed = dimensions[j].placed.as_ref().unwrap();
        let want = Want {
            id: dimensions[j].id.clone(),
            views: Vec::new(),
            value: dimensions[j].value,
            terms: dimensions[j].terms.clone(),
            measurements: dimensions[j].measurements.clone(),
            basis: dimensions[j].basis.clone(),
            shape: Shape::Callout {
                arcs: Vec::new(),
                sites: Vec::new(),
            },
            turning: None,
        };
        dimensions[j].placed = Some(super::placement::relabel(placed, &was, &want));
        dimensions[i].placed = None;
        dimensions[i].state = crate::items::State::Covered {
            by: dimensions[j].id.clone(),
        };
    }
}

/// Only after a default is proven drawable may it replace equal fillet leaders.
pub(crate) fn cover_fillets(
    notes: &mut [crate::notes::PlannedNote],
    dimensions: &mut [super::PlannedDimension],
    spec: &crate::model::Spec,
    given: &crate::model::Spec,
) {
    for note in notes.iter_mut().filter(|n| {
        n.placed.is_some()
            && crate::label_text::note_body(spec, n).starts_with("ALL UNSPECIFIED RADII")
    }) {
        let Some(value) = note.measurements.first().map(|m| m.value) else {
            continue;
        };
        for d in dimensions.iter_mut().filter(|d| {
            d.placed.is_some()
                && !d.measurements.is_empty()
                && d.measurements.iter().all(|m| {
                    matches!(
                        m.parameter.as_str(),
                        "fillet.radius" | "cylindrical_profile.radius"
                    )
                })
                && fmt(d.value) == fmt(value)
                && !overridden(spec, given, &d.id)
        }) {
            note.measurements.extend(d.measurements.clone());
            d.placed = None;
            d.state = crate::items::State::Covered {
                by: note.id.clone(),
            };
        }
    }
}

/// Unlike a representative site, each row locates exactly one physical radius centre.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadiusRow {
    pub dimension: String,
    pub feature: String,
    pub view: String,
    pub centre: [f64; 2],
    pub radius: f64,
}

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadiusTable {
    pub rows: Vec<RadiusRow>,
    pub table: crate::tables::PlannedTable,
}

fn table_spec(rows: &[RadiusRow]) -> crate::tables::TableSpec {
    let mut cells = vec![vec![
        "RADIUS CENTRES".into(),
        "X FROM VIEW LEFT".into(),
        "Y FROM VIEW BOTTOM".into(),
        "RADIUS".into(),
    ]];
    cells.extend(rows.iter().map(|r| {
        vec![
            r.view.trim_start_matches("v:").to_uppercase(),
            crate::lint::parameters::fmt(r.centre[0]),
            crate::lint::parameters::fmt(r.centre[1]),
            format!("R{}", fmt(r.radius)),
        ]
    }));
    crate::tables::TableSpec {
        sheet: None,
        id: RADIUS_TABLE.into(),
        rows: cells.into_iter().map(serde_json::Value::from).collect(),
        prefer: Some("tr".into()),
    }
}

/// New notch bands must leave room for the existing centre-located radius schedule.
/// Forecast only automatic groups of at least four exact, end-on centres in one view;
/// counted fillet owners and authored radius presentations retain their normal grammar.
pub(crate) fn reservations(
    a: &Analysis,
    spec: &crate::model::Spec,
) -> Vec<crate::tables::TableSpec> {
    if spec.dimension_source != crate::model::DimensionSource::Auto
        || crate::notches::requirements(a).is_empty()
    {
        return Vec::new();
    }
    let groups = groups(a);
    let general = general(&groups);
    let mut rows = Vec::new();
    for (i, group) in groups.iter().enumerate() {
        // Invariant: Groups are nonempty and contain only features with a read radius.
        let value = radius(group[0]).unwrap();
        let id = want_id(&group[0].id, "blend.radius");
        if Some(i) == general
            || group.len() < 4
            || group.iter().any(|f| {
                f.parameters.get("side").and_then(serde_json::Value::as_str) != Some("concave")
            })
            || overridden(spec, spec, &id)
            || a.features
                .iter()
                .any(|f| f.kind == "fillet" && radius(f).is_some_and(|r| fmt(r) == fmt(value)))
        {
            continue;
        }
        let located = group
            .iter()
            .map(|f| {
                let (view, centre) = crate::lint::blends::table_centre(a, f)?;
                (!spec.views.get(view).is_some_and(|v| v.omit)).then(|| RadiusRow {
                    dimension: id.clone(),
                    feature: f.id.clone(),
                    view: format!("v:{view}"),
                    centre,
                    radius: value,
                })
            })
            .collect::<Option<Vec<_>>>();
        if let Some(located) = located
            && located.iter().all(|r| r.view == located[0].view)
        {
            rows.extend(located);
        }
    }
    if rows.is_empty() {
        Vec::new()
    } else {
        vec![table_spec(&rows)]
    }
}

/// Tables replace obstructed automatic exception leaders only when every member has a
/// proven straight radius axis seen end-on. All centres are listed: no representative
/// XYZ point is allowed to locate an arbitrary equal-radius subset. Authored ink stays literal.
#[allow(clippy::too_many_arguments)]
pub(crate) fn tabulate_obstructed(
    a: &Analysis,
    spec: &crate::model::Spec,
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    dimensions: &mut [super::PlannedDimension],
    gdt: &[crate::dims::gdt::PlannedGdt],
    problems: &mut Vec<Problem>,
    complete_views: bool,
) -> Option<RadiusTable> {
    let recesses = dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"))
    });
    // The bare planner keeps its established estimated schedule. Complete-view
    // recovery judges the finished routes: multiple counted groups alone are
    // not an obstruction and must not withdraw clear representative leaders.
    let mut counted_by_view = BTreeMap::<&str, usize>::new();
    if recesses && !complete_views {
        for d in dimensions.iter().filter(|d| {
            d.placed.is_some()
                && d.measurements.len() > 1
                && d.measurements.iter().all(|m| m.parameter == "blend.radius")
        }) {
            *counted_by_view.entry(&d.view).or_default() += 1;
        }
    }
    let mut indices = Vec::new();
    let mut rows = Vec::new();
    for (i, d) in dimensions.iter().enumerate() {
        if d.placed.is_none()
            || d.measurements.is_empty()
            || !d.measurements.iter().all(|m| m.parameter == "blend.radius")
            || spec.precision.contains_key(&d.id)
            || d.measurements.iter().any(|m| {
                spec.precision
                    .contains_key(&want_id(&m.feature, &m.parameter))
            })
            || spec.dimensions.get(&d.id).is_some_and(|s| {
                s.place.is_some() || s.tolerance.is_some() || s.text.is_some() || s.style.is_some()
            })
            || (!(d.measurements.len() > 1
                && counted_by_view.get(d.view.as_str()).is_some_and(|n| *n > 1))
                && !problems.iter().any(|p| {
                    p.subjects.contains(&d.id) && leader_problem(&p.code)
                    // A clear leader beyond an estimated band stays visible. A
                    // reservation warning alone does not justify a radius table.
                    && (!recesses || p.code != "annotation_outside_area")
                }))
        {
            continue;
        }
        let mut group = Vec::new();
        for m in &d.measurements {
            let f = a.features.iter().find(|f| f.id == m.feature)?;
            let Some((view, centre)) = crate::lint::blends::table_centre(a, f) else {
                break;
            };
            if !views.iter().any(|v| v.id == format!("v:{view}")) {
                break;
            }
            group.push(RadiusRow {
                dimension: d.id.clone(),
                feature: f.id.clone(),
                view: format!("v:{view}"),
                centre,
                radius: m.value,
            });
        }
        if group.len() == d.measurements.len() {
            indices.push(i);
            rows.extend(group);
        }
    }
    if rows.is_empty() {
        return None;
    }
    rows.sort_by(|a, b| {
        a.view
            .cmp(&b.view)
            .then(a.radius.total_cmp(&b.radius))
            .then(a.centre[0].total_cmp(&b.centre[0]))
            .then(a.centre[1].total_cmp(&b.centre[1]))
            .then(a.feature.cmp(&b.feature))
    });
    let specs = [table_spec(&rows)];
    let mut remaining: Vec<_> = dimensions
        .iter()
        .enumerate()
        .filter(|(i, _)| !indices.contains(i))
        .map(|(_, d)| d.clone())
        .collect();
    remaining.extend(gdt.iter().filter_map(crate::dims::gdt::proxy));
    let (mut tables, _) = crate::tables::plan(&specs, a, sheet, views, &remaining, None, spec);
    let mut table = tables.pop()?;
    table.placed.as_ref()?;
    table.default = true;
    table.basis = "every obstructed radius occurrence has its own centre, in model millimetres from the left and bottom of its named view's envelope; no location is borrowed from a representative".into();
    for i in indices {
        let d = &mut dimensions[i];
        d.placed = None;
        d.state = crate::items::State::Covered {
            by: RADIUS_TABLE.into(),
        };
        problems.retain(|p| !p.subjects.contains(&d.id) || !leader_problem(&p.code));
    }
    Some(RadiusTable { rows, table })
}

#[cfg(test)]
mod identity_tests {
    use super::*;

    #[test]
    fn a_new_id_spelling_keeps_the_same_counted_radius_representative() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let (_, mut a) = crate::model::analyse_native(&path).unwrap();
        let mut first = a.features[0].clone();
        first.kind = "blend".into();
        first.id = "f:blend:f7".into();
        first.legacy_id = Some("f:blend:1".into());
        first.faces = vec![7];
        first
            .parameters
            .insert("radius".into(), serde_json::json!(0.25));
        let mut second = first.clone();
        second.id = "f:blend:f14".into();
        second.legacy_id = Some("f:blend:2".into());
        second.faces = vec![14];
        a.features = vec![first, second];
        let groups = groups(&a);
        assert_eq!(groups.len(), 1);
        assert_eq!(
            groups[0].iter().map(|f| f.id.as_str()).collect::<Vec<_>>(),
            ["f:blend:f7", "f:blend:f14"]
        );
    }
}
