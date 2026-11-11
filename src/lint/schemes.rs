//! Re-measure every printed scheme link and prove connectivity before granting derived roles.
//! No Spec or planner output is read: losing one link loses its coverage.
use super::{
    LintIssue, Mark, Severity, holes,
    redundancy::{Span, path},
};
use crate::{model::Analysis, output::Claim};
use std::collections::BTreeMap;

type Graph = (usize, Option<String>);

pub(crate) fn evidence(a: &Analysis, marks: &[Mark]) -> (Vec<Mark>, Vec<LintIssue>) {
    if !marks
        .iter()
        .any(|m| m.claims.iter().any(|c| c.feature.starts_with("scheme:")))
    {
        return (Vec::new(), Vec::new());
    }
    let mut graphs: BTreeMap<Graph, Vec<(Span, usize)>> = BTreeMap::new();
    let mut issues = Vec::new();
    for (i, m) in marks.iter().enumerate() {
        for c in &m.claims {
            let Some(key) = c.feature.strip_prefix("scheme:") else {
                continue;
            };
            let fields: Vec<_> = key.split('|').collect();
            let measured = (|| {
                let [name, datum, from, to] = fields.as_slice() else {
                    return None;
                };
                let (style, axis) = c.parameter.split_once('.')?;
                if !["chain", "baseline", "ordinate"].contains(&style) {
                    return None;
                }
                let k = ["x", "y", "z"].iter().position(|&a| a == axis)?;
                a.scheme_station(datum, k).ok()?;
                let p = a.scheme_station(from, k).ok()?;
                let q = a.scheme_station(to, k).ok()?;
                let value = if style == "ordinate" {
                    q[k] - p[k]
                } else {
                    (q[k] - p[k]).abs()
                };
                let nominal = m.text.split_whitespace().next()?;
                let decimals = nominal
                    .split_once('.')
                    .map_or(0, |(_, digits)| digits.len());
                let truthful = nominal.parse::<f64>().is_ok_and(|n| {
                    n.is_finite()
                        && (n - m.units.from_mm(value)).abs()
                            <= 0.5 * 10f64.powi(-(decimals as i32)) + 1e-9
                });
                if !truthful || m.text.starts_with('(') {
                    return None;
                }
                Some((
                    name.to_string(),
                    datum.to_string(),
                    Span::new(k, p[k], q[k]),
                ))
            })();
            let Some((_, _, span)) = measured else {
                issues.push(LintIssue {id:format!("l:dimension_scheme_invalid:{}",m.name), code:"dimension_scheme_invalid".into(),severity:Severity::Error,
                    message:format!("{} does not state the measured distance between its scheme references; restore its nominal or correct the references and regenerate.",m.name),
                    annotation:Some(m.name.clone()), subjects:vec![m.name.clone()], view:m.view.clone(), sheet:None, at:None, requirements:vec![], measurements:vec![], sources:vec![],stage:None,related:vec![],evidence:None,fix:Some("Correct the scheme references or regenerate its measured ink.".into()) });
                continue;
            };
            let edges = graphs.entry((span.axis, m.view.clone())).or_default();
            if let Some(route) = path(edges, span) {
                issues.push(LintIssue {id:format!("l:closed_dimension_chain:{}",m.name),code:"closed_dimension_chain".into(),severity:Severity::Warning,
                    message:"The printed scheme closes an already dimensioned station chain; remove its redundant link.".into(),annotation:Some(m.name.clone()),
                    subjects:vec![m.name.clone()],related:route.iter().map(|&j|marks[j].name.clone()).collect(),sheet:None, at:None,view:m.view.clone(),requirements:vec![],measurements:vec![],sources:vec![],stage:None,evidence:None,fix:Some("Remove the redundant scheme link.".into())});
            }
            edges.push((span, i));
        }
    }
    let mut extra = Vec::new();
    for ((k, view), mut edges) in graphs {
        // A different chosen datum remains related to the minimum end by the ordinary
        // controlling overall, or another truthful support-plane interval on the sheet.
        for (i, m) in marks.iter().enumerate() {
            if m.text.starts_with('(') {
                continue;
            }
            for c in &m.claims {
                for s in supports(a, &c.feature, &c.parameter, &m.text, m.units)
                    .into_iter()
                    .filter(|s| s.axis == k)
                {
                    if !edges.iter().any(|(p, j)| *j == i && p.same(s)) {
                        if let Some(route) = path(&edges, s)
                            && route.iter().any(|&j| {
                                marks[j]
                                    .claims
                                    .iter()
                                    .any(|c| c.feature.starts_with("scheme:"))
                            })
                        {
                            let mut issue=super::uncovered(a,"closed_dimension_chain",&c.feature,&c.parameter,"A printed dimension closes the authored station network; remove a redundant controller.".into());
                            issue.annotation = Some(m.name.clone());
                            issue.related = route.iter().map(|&j| marks[j].name.clone()).collect();
                            issues.push(issue);
                        }
                        edges.push((s, i));
                    }
                }
            }
        }
        let mut roles = Vec::new();
        let parameter = super::envelope::PARAMETERS[k];
        roles.push((
            "envelope".to_string(),
            parameter.to_string(),
            Span::new(k, a.envelope[0][k], a.envelope[1][k]),
        ));
        for f in &a.features {
            if f.kind == "turned_step" {
                if let Ok(b) = super::turned::band(&crate::bridge::holes::frame(a), f)
                    && b.axis == k
                {
                    roles.push((f.id.clone(), "step.length".into(), Span::new(k, b.lo, b.hi)));
                }
            } else if f.fingerprint.axis.is_some_and(|axis| axis[k].abs() < 1e-8)
                && ["hole", "hole_pattern", "boss", "polygonal_boss"].contains(&f.kind.as_str())
            {
                roles.push((
                    f.id.clone(),
                    format!("location.length.{}", ["x", "y", "z"][k]),
                    Span::new(k, a.envelope[0][k], f.fingerprint.position[k]),
                ));
            }
            for role in ["location_slot.length", "location_slot_across.length"] {
                if let Some(s) = super::redundancy::span(a, &f.id, role)
                    && s.axis == k
                {
                    roles.push((f.id.clone(), role.into(), s));
                }
            }
        }
        for n in super::notch_evidence::requirements(a) {
            if [n.width_axis, n.depth_axis].contains(&k) {
                roles.push((
                    n.id,
                    format!("location.length.{}", ["x", "y", "z"][k]),
                    Span::new(k, a.envelope[0][k], n.mouth[k]),
                ));
            }
        }
        for c in marks.iter().flat_map(|m| &m.claims) {
            if let Some(feature) = c.feature.strip_prefix("scheme_owns:")
                && let Some(span) = super::redundancy::span(a, feature, &c.parameter)
                && span.axis == k
            {
                roles.push((feature.to_string(), c.parameter.clone(), span));
            }
        }
        // Slot-pattern requirements retain their vocabulary, but their location and pitch
        // can be determined by independently verified physical-member coordinates.
        for f in a.features.iter().filter(|f| f.kind == "slot_pattern") {
            let members: Vec<_> = f
                .parameters
                .get("slots")
                .and_then(|v| v.as_array())
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str())
                .filter_map(|id| a.scheme_station(id, k).ok())
                .collect();
            let origin = a.envelope[0];
            if members.len() < 2
                || !members
                    .iter()
                    .all(|p| path(&edges, Span::new(k, origin[k], p[k])).is_some())
            {
                continue;
            }
            if let Ok(wanted) = super::slots::pattern_wanted(a, f) {
                for w in wanted {
                    let applies = w.parameter
                        == format!("location_slot_pattern.length.{}", ["x", "y", "z"][k])
                        || w.parameter
                            == format!("grid_pitch.length.{}", if k == 0 { "col" } else { "row" })
                        || w.parameter == "pitch.length"
                            && members.windows(2).any(|p| (p[0][k] - p[1][k]).abs() > 1e-6);
                    if applies && let Ok(values) = w.values {
                        extra.push(Mark {
                            precision: None,
                            units: Default::default(),
                            name: format!("derived:{}/{}", f.id, w.parameter),
                            view: view.clone(),
                            text: values
                                .into_iter()
                                .map(super::parameters::fmt)
                                .collect::<Vec<_>>()
                                .join(" "),
                            claims: vec![Claim {
                                feature: f.id.clone(),
                                parameter: w.parameter,
                            }],
                        });
                    }
                }
            }
        }
        for (feature, parameter, span) in roles {
            let target = span;
            if let Some(route) = path(&edges, target)
                && !route.is_empty()
                && route.iter().any(|&i| {
                    marks[i]
                        .claims
                        .iter()
                        .any(|c| c.feature.starts_with("scheme:"))
                })
                && !extra.iter().any(|m: &Mark| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == feature && c.parameter == parameter)
                })
            {
                if parameter.starts_with("location.length.")
                    && a.recognition.as_ref().is_some_and(|r| {
                        r.patterns.iter().any(|p| {
                            p.members.contains(&feature)
                                && route.iter().any(|&i| {
                                    marks[i].claims.iter().any(|c| {
                                        c.feature == p.id && c.parameter.contains("pitch.length")
                                    })
                                })
                        })
                    })
                {
                    continue;
                }
                extra.push(Mark {
                    precision: None,
                    units: Default::default(),
                    name: format!("derived:{feature}/{parameter}"),
                    view: view.clone(),
                    text: super::parameters::fmt(span.hi - span.lo),
                    claims: vec![Claim { feature, parameter }],
                });
            }
        }
    }
    (extra, issues)
}

/// Physical supports actually certified by one printed claim. Counted pitches certify
/// every interval of the established pattern, rather than only the representative span.
pub(crate) fn supports(
    a: &Analysis,
    feature: &str,
    parameter: &str,
    text: &str,
    units: crate::output::Units,
) -> Vec<Span> {
    let text = text.trim();
    if text.starts_with('(') {
        return Vec::new();
    }
    let (count, nominal) = text
        .split_once('×')
        .and_then(|(n, rest)| {
            n.trim()
                .parse::<usize>()
                .ok()
                .map(|n| (n, rest.trim_start()))
        })
        .unwrap_or((1, text));
    let nominal = nominal.split_whitespace().next().unwrap_or("");
    if let Some(s) = super::redundancy::span(a, feature, parameter) {
        return holes::states_in(nominal, s.hi - s.lo, units)
            .then_some(s)
            .into_iter()
            .collect();
    }
    let f = a.features.iter().find(|f| f.id == feature);
    let pattern = a
        .recognition
        .as_ref()
        .and_then(|r| r.patterns.iter().find(|p| p.id == feature));
    let ids: Vec<&str> = if let Some(p) = pattern {
        p.members.iter().map(String::as_str).collect()
    } else {
        f.and_then(|f| f.parameters.get("slots"))
            .and_then(|v| v.as_array())
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str())
            .collect()
    };
    let members: Option<Vec<_>> = ids
        .iter()
        .map(|id| a.features.iter().find(|f| f.id == *id))
        .collect();
    let members = members.unwrap_or_default();
    if parameter.starts_with("location.length.") {
        let Some(k) = ["x", "y", "z"]
            .iter()
            .position(|axis| parameter == format!("location.length.{axis}"))
        else {
            return Vec::new();
        };
        let stations: Vec<_> = if pattern.is_some_and(|p| p.arrangement == "linear") {
            members.iter().map(|f| f.fingerprint.position[k]).collect()
        } else {
            f.map(|f| vec![f.fingerprint.position[k]])
                .unwrap_or_default()
        };
        let mut spans: Vec<_> = stations
            .into_iter()
            .map(|x| Span::new(k, a.envelope[0][k], x))
            .filter(|s| holes::states_in(nominal, s.hi - s.lo, units))
            .collect();
        spans.sort_by(|p, q| p.hi.total_cmp(&q.hi));
        spans.dedup_by(|p, q| p.same(*q));
        return if spans.len() == 1 { spans } else { Vec::new() };
    }
    if !parameter.contains("pitch.length")
        || members.len() < 2
        || pattern.is_some_and(|p| p.arrangement == "bolt_circle")
    {
        return Vec::new();
    }
    let normal = members[0]
        .fingerprint
        .axis
        .and_then(crate::bridge::holes::principal);
    let Some(normal) = normal else {
        return Vec::new();
    };
    let axes: Vec<_> = (0..3).filter(|&k| k != normal).collect();
    let stations = |k: usize| {
        let mut xs: Vec<_> = members.iter().map(|f| f.fingerprint.position[k]).collect();
        xs.sort_by(f64::total_cmp);
        xs.dedup_by(|x, y| (*x - *y).abs() < 1e-6);
        xs
    };
    let k = if parameter.ends_with(".col") {
        Some(axes[0])
    } else if parameter.ends_with(".row") {
        Some(axes[1])
    } else if stations(axes[0]).len() > 1 && stations(axes[1]).len() == 1 {
        Some(axes[0])
    } else if stations(axes[1]).len() > 1 && stations(axes[0]).len() == 1 {
        Some(axes[1])
    } else {
        None
    };
    let Some(k) = k else { return Vec::new() };
    let xs = stations(k);
    if xs.len() < 2 || count != xs.len() - 1 {
        return Vec::new();
    }
    let pitch = xs[1] - xs[0];
    if !holes::states_in(nominal, pitch, units)
        || xs
            .windows(2)
            .any(|p| ((p[1] - p[0]) - pitch).abs() > super::redundancy::TOL)
    {
        return Vec::new();
    }
    xs.windows(2).map(|p| Span::new(k, p[0], p[1])).collect()
}
