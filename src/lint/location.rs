//! Rooted physical station graphs. Both precedence and the drawn-set audit use this walk.
use super::{LintIssue, Mark, uncovered};
use crate::model::Analysis;
use crate::pmi::{Category, Reading};
use std::collections::BTreeSet;

const EPS: f64 = 1e-6;

/// The part and witnesses use millimetres; authored PMI can explicitly print inches.
fn states_mm(text: &str, value: f64) -> bool {
    let inch = text
        .split_whitespace()
        .any(|word| word.eq_ignore_ascii_case("INCH") || word.eq_ignore_ascii_case("INCHES"));
    super::holes::states(text, value / if inch { 25.4 } else { 1. })
}
pub(crate) const AXES: [&str; 3] = ["x", "y", "z"];
#[derive(Clone, Debug)]
pub(crate) struct Edge {
    pub axis: usize,
    pub a: f64,
    pub b: f64,
    pub owner: String,
    pub a_feature: Option<String>,
    pub b_feature: Option<String>,
}

/// The shortest chain to any root. Parallel authored edges remain separate edges.
pub(crate) fn route(
    edges: &[Edge],
    roots: &[f64],
    axis: usize,
    at: f64,
    feature: Option<&str>,
    excluded: &BTreeSet<usize>,
) -> Option<Vec<usize>> {
    let mut queue = vec![(at, feature.map(str::to_string), Vec::new())];
    let mut seen = vec![(at, feature.map(str::to_string))];
    let mut cursor = 0;
    while cursor < queue.len() {
        let (p, node, path) = queue[cursor].clone();
        cursor += 1;
        // A feature on an origin plane needs no additional zero-length dimension.
        // Analytic feature axes use the tighter tolerance; rounded planes use TOL.
        if roots.iter().any(|r| {
            (r - p).abs()
                < if node.is_none() {
                    super::redundancy::TOL
                } else {
                    EPS
                }
        }) {
            return Some(path);
        }
        for (i, e) in edges.iter().enumerate() {
            if e.axis != axis || excluded.contains(&i) {
                continue;
            }
            let (q, next_node) = if node == e.a_feature && (node.is_some() || (p - e.a).abs() < EPS)
            {
                (e.b, e.b_feature.clone())
            } else if node == e.b_feature && (node.is_some() || (p - e.b).abs() < EPS) {
                (e.a, e.a_feature.clone())
            } else {
                continue;
            };
            if seen
                .iter()
                .any(|(v, n)| *n == next_node && (n.is_some() || (q - v).abs() < EPS))
            {
                continue;
            }
            seen.push((q, next_node.clone()));
            let mut next = path.clone();
            next.push(i);
            queue.push((q, next_node, next));
        }
    }
    None
}

pub(crate) fn roots(a: &Analysis) -> [Vec<f64>; 3] {
    let mut roots = std::array::from_fn(|k| vec![a.envelope[0][k], a.envelope[1][k]]);
    if let Some(Reading::Read(r)) = &a.pmi {
        for record in &r.records {
            if record.category == Category::Datum
                && let Some(g) = &record.geometry
                && g.surface.as_deref() == Some("plane")
                && let Some(k) = ["X", "Y", "Z"]
                    .iter()
                    .position(|v| Some(*v) == g.axis.as_deref())
                && let Some(p) = g.sites.first()
            {
                roots[k].push(p[k]);
            }
        }
    }
    roots
}

/// Explicit drawing origins are references whose stations can be re-measured on Analysis.
pub(crate) fn drawing_roots(a: &Analysis, marks: &[Mark]) -> [Vec<f64>; 3] {
    let mut out = roots(a);
    for c in marks.iter().flat_map(|m| &m.claims) {
        if let Some(key) = c.feature.strip_prefix("scheme:")
            && let [_, datum, _, _] = key.split('|').collect::<Vec<_>>().as_slice()
            && let Some((style, axis)) = c.parameter.split_once('.')
            && ["chain", "baseline", "ordinate"].contains(&style)
            && let Some(k) = AXES.iter().position(|v| *v == axis)
            && let Ok(p) = a.scheme_station(datum, k)
        {
            out[k].push(p[k]);
        }
        if let Some(word) = c.feature.strip_prefix("station_root:")
            && let Some((axis, value)) = word.split_once('|')
            && let (Ok(k), Ok(v)) = (axis.parse::<usize>(), value.parse::<f64>())
            && k < 3
            && v.is_finite()
        {
            out[k].push(v);
        }
        if let Some(word) = c.feature.strip_prefix("station_origin:")
            && let Some((axis, reference)) = word.split_once('|')
            && let Ok(k) = axis.parse::<usize>()
            && k < 3
        {
            let station = if let Some(f) = reference
                .strip_prefix("face:")
                .and_then(|s| s.parse::<usize>().ok())
                .and_then(|i| a.face_attachments.get(i))
            {
                if f.surface.as_deref() == Some("plane") && f.axis == Some(k) {
                    f.sites.first().map(|p| p[k])
                } else {
                    None
                }
            } else {
                a.scheme_station(reference, k).ok().map(|p| p[k])
            };
            if let Some(v) = station {
                out[k].push(v);
            }
        }
    }
    out
}

/// Physical spans retained as drawing claims. The audit validates their nominal against ink;
/// these are station constraints, not inferred coverage from a dimension's prose.
pub(crate) fn span_claim(axis: usize, a: f64, b: f64) -> String {
    format!("station_span:{axis}|{a}|{b}")
}

pub(crate) fn edges(a: &Analysis, marks: &[Mark]) -> Vec<Edge> {
    build_edges(a, marks, false)
}

/// Extended drawing evidence is local to lint; planner precedence keeps its existing graph.
pub(crate) fn drawn_edges(a: &Analysis, marks: &[Mark]) -> Vec<Edge> {
    build_edges(a, marks, true)
}

fn build_edges(a: &Analysis, marks: &[Mark], authored: bool) -> Vec<Edge> {
    let mut out = Vec::new();
    let report = match &a.pmi {
        Some(Reading::Read(r)) => Some((r, r.identities())),
        _ => None,
    };
    for mark in marks.iter().filter(|m| !m.text.starts_with('(')) {
        let mut spans = Vec::new();
        let ink = mark.claims.iter().any(|c| c.feature == "station_ink");
        let physical = ink
            || mark
                .claims
                .iter()
                .any(|c| c.feature.starts_with("station_span:") || c.feature.starts_with("pmi:"));
        for c in &mark.claims {
            if let Some(word) = c.feature.strip_prefix("station_span:") {
                let words: Vec<_> = word.split('|').collect();
                if let [k, p, q] = words.as_slice()
                    && let (Ok(k), Ok(p), Ok(q)) =
                        (k.parse::<usize>(), p.parse::<f64>(), q.parse::<f64>())
                    && k < 3
                    && p.is_finite()
                    && q.is_finite()
                    && (states_mm(&mark.text, (q - p).abs())
                        || mark.text.split_once('×').is_some_and(|(n, rest)| {
                            n.trim()
                                .parse::<f64>()
                                .is_ok_and(|n| states_mm(rest.trim(), (q - p).abs() / n))
                        }))
                {
                    spans.push((k, p, q));
                }
            } else if !ink
                && let Some((r, ids)) = &report
                && c.feature.starts_with("pmi:")
            {
                for record in r
                    .records
                    .iter()
                    .filter(|r| ids.id(&r.source_id).ok() == Some(c.feature.as_str()))
                {
                    if let Some(g) = &record.geometry
                        && g.stations.len() == 2
                        && let Some(k) = ["X", "Y", "Z"]
                            .iter()
                            .position(|v| Some(*v) == g.axis.as_deref())
                        && states_mm(&mark.text, (g.stations[1][k] - g.stations[0][k]).abs())
                    {
                        spans.push((k, g.stations[0][k], g.stations[1][k]));
                    }
                }
            } else if !physical {
                if authored && let Some((k, p, q)) = authored_span(a, mark, c) {
                    spans.push((k, p, q));
                }
                // A compound pocket callout states each claimed numeric size, even when
                // that size is not the label's leading number.
                if authored
                    && ["pocket_width.length", "pocket_length.length"]
                        .contains(&c.parameter.as_str())
                    && let Some(s) = super::redundancy::span(a, &c.feature, &c.parameter)
                    && states_mm(mark.text.split('±').next().unwrap_or(""), s.hi - s.lo)
                {
                    spans.push((s.axis, s.lo, s.hi));
                }
                spans.extend(
                    super::schemes::supports(a, &c.feature, &c.parameter, &mark.text)
                        .into_iter()
                        .map(|s| (s.axis, s.lo, s.hi)),
                );
            }
        }
        // Invariant: Spans come from successfully parsed finite coordinates.
        spans.sort_by(|x, y| x.partial_cmp(y).unwrap());
        spans.dedup_by(|x, y| x.0 == y.0 && (x.1 - y.1).abs() < EPS && (x.2 - y.2).abs() < EPS);
        out.extend(
            spans
                .into_iter()
                .filter(|(_, p, q)| (p - q).abs() > EPS)
                .map(|(axis, p, q)| Edge {
                    axis,
                    a: p,
                    b: q,
                    owner: mark.name.clone(),
                    a_feature: None,
                    b_feature: None,
                }),
        );
    }
    let mut typed = Vec::new();
    for mark in marks {
        let spans = out
            .iter()
            .filter(|e| e.owner == mark.name)
            .cloned()
            .collect();
        typed.extend(identify_impl(a, mark, spans, authored));
    }
    if !authored {
        return typed;
    }
    // A printed interval fixes its midpoint relative to either end. Its two
    // halves retain the original owner, so they cannot manufacture a second
    // independent controller. No length in the Analysis alone adds an edge.
    for (f, p) in super::pockets::placed(a) {
        let Ok(p) = p else { continue };
        for role in ["pocket_width.length", "pocket_length.length"] {
            let Some(s) = super::redundancy::span(a, &f.id, role) else {
                continue;
            };
            let intervals: Vec<_> = typed
                .iter()
                .filter(|e| {
                    e.axis == s.axis
                        && e.a_feature.is_none()
                        && e.b_feature.is_none()
                        && ((e.a.min(e.b) - s.lo).abs() < EPS)
                        && ((e.a.max(e.b) - s.hi).abs() < EPS)
                })
                .cloned()
                .collect();
            let centre = if s.axis == p.long_axis {
                (p.lo + p.hi) / 2.
            } else {
                p.w_center
            };
            for e in intervals {
                for end in [e.a, e.b] {
                    typed.push(Edge {
                        a: end,
                        b: centre,
                        a_feature: None,
                        b_feature: None,
                        ..e.clone()
                    });
                }
            }
        }
    }
    typed
}

/// Authored evidence must state the re-measured nominal. A true distance only
/// enters this graph if the supports also have unique stations on that axis;
/// an oblique length never becomes its nominal projection.
fn authored_span(a: &Analysis, mark: &Mark, c: &crate::output::Claim) -> Option<(usize, f64, f64)> {
    let refs: Vec<_> = c
        .feature
        .strip_prefix("geometry:")?
        .split('|')
        .map(String::from)
        .collect();
    let along = c.parameter.strip_prefix("linear.")?;
    let m = a.measure_references(&refs, "linear", Some(along)).ok()?;
    if !states_mm(mark.text.split_whitespace().next()?, m.value) {
        return None;
    }
    let [p, q] = m.witnesses?;
    let k = AXES
        .iter()
        .position(|axis| *axis == along)
        .or_else(|| (0..3).find(|&k| (0..3).all(|j| j == k || (q[j] - p[j]).abs() < EPS)))?;
    let principal = a.measure_references(&refs, "linear", Some(AXES[k])).ok()?;
    ((principal.value - m.value).abs() < EPS).then_some((k, p[k], q[k]))
}

/// Re-measure a principal-axis constraint from the actual two witnesses and the drawn view
/// transform. This also works for cropped details: the frame names the whole-part centre.
pub fn ink_span(
    a: &Analysis,
    frame: &crate::output::DrawnFrame,
    lines: &[[[f64; 2]; 2]],
) -> Option<(usize, f64, f64)> {
    let ink = crate::output::linear_ink(lines)?;
    if !frame.scale.is_finite() || frame.scale <= 0. {
        return None;
    }
    let view = haecceity::hlr::View::new(frame.direction, frame.up)?;
    let (axis, sign) = (0..3).find_map(|k| {
        let mut unit = [0.; 3];
        unit[k] = 1.;
        let q = view.map(unit);
        ((q[ink.k].abs() - 1.).abs() < EPS && q[1 - ink.k].abs() < EPS).then_some((k, q[ink.k]))
    })?;
    let station = |e: &[[f64; 2]; 2]| {
        let run = e[1][1 - ink.k] - e[0][1 - ink.k];
        let p = e[0][ink.k]
            + (lines[2][0][1 - ink.k] - e[0][1 - ink.k]) * (e[1][ink.k] - e[0][ink.k]) / run;
        0.5 * (a.envelope[0][axis] + a.envelope[1][axis])
            + sign * (p - frame.at[ink.k]) / frame.scale
    };
    Some((axis, station(&lines[0]), station(&lines[1])))
}

pub(crate) fn witness_owners(a: &Analysis, claims: &[crate::output::Claim]) -> BTreeSet<String> {
    let mut owners = BTreeSet::new();
    for claim in claims {
        if let Some(p) = pattern(a, &claim.feature) {
            owners.extend(p.members.iter().cloned());
        } else if a.features.iter().any(|f| f.id == claim.feature) {
            owners.insert(claim.feature.clone());
        }
    }
    owners
}

/// A direct pitch can use the hole's drawn centre line in place of a
/// separate extension. Reconstruct only a collapsed witness whose arrow tip
/// and stored point both lie on that same, independently drawn centre line.
pub(crate) fn centreline_witnesses(
    lines: &[[[f64; 2]; 2]],
    heads: &[[[f64; 2]; 2]],
    centres: &[[[f64; 2]; 2]],
) -> Option<Vec<[[f64; 2]; 2]>> {
    if lines.len() < 3 {
        return None;
    }
    let d = lines[2];
    let k = (0..2)
        .find(|&k| (d[0][1 - k] - d[1][1 - k]).abs() < EPS && (d[0][k] - d[1][k]).abs() > EPS)?;
    let near = |p: [f64; 2], q: [f64; 2]| (p[0] - q[0]).hypot(p[1] - q[1]) < EPS;
    let mut out = lines.to_vec();
    for witness in out.iter_mut().take(2) {
        if !near(witness[0], witness[1]) {
            continue;
        }
        let p = witness[0];
        let mut tip = p;
        tip[1 - k] = d[0][1 - k];
        if heads
            .iter()
            .filter(|s| near(s[0], tip) || near(s[1], tip))
            .count()
            < 2
        {
            return None;
        }
        let support = centres.iter().find(|s| {
            (s[0][k] - p[k]).abs() < EPS
                && (s[1][k] - p[k]).abs() < EPS
                && [p[1 - k], tip[1 - k]].iter().all(|v| {
                    *v >= s[0][1 - k].min(s[1][1 - k]) - EPS
                        && *v <= s[0][1 - k].max(s[1][1 - k]) + EPS
                })
                && (s[0][1 - k] - s[1][1 - k]).abs() > EPS
        })?;
        *witness = *support;
    }
    crate::output::linear_ink(&out)?;
    Some(out)
}

/// Ink evidence is local to lint. It is never written into the drawing or its Spec.
pub(crate) fn drawn_constraints(
    a: &Analysis,
    drawing: &crate::output::Drawing,
    marks: &mut [Mark],
) {
    if drawing.sheet.frames.is_empty() {
        return;
    }
    for mark in marks {
        let Some(item) = drawing.sheet.items.iter().find(|d| d.name == mark.name) else {
            continue;
        };
        if item.kind != crate::output::Kind::Dimension || item.elbow.is_some() {
            continue;
        }
        if let Some(origin) = drawing
            .annotations
            .iter()
            .find(|a| a.name == mark.name)
            .and_then(|a| a.coordinate_origin)
        {
            for (k, v) in origin.into_iter().enumerate() {
                mark.claims.push(crate::output::Claim {
                    feature: format!("station_root:{k}|{v}"),
                    parameter: "origin".into(),
                });
            }
        }
        mark.claims
            .retain(|c| !c.feature.starts_with("station_span:") && c.feature != "station_ink");
        mark.claims.push(crate::output::Claim {
            feature: "station_ink".into(),
            parameter: "witnesses".into(),
        });
        let frame = mark
            .view
            .as_deref()
            .and_then(|v| drawing.sheet.frames.get(v.trim_start_matches("v:")));
        let span = frame.and_then(|f| ink_span(a, f, &item.lines)).or_else(|| {
            let owners = witness_owners(a, &mark.claims);
            let centres: Vec<_> = drawing
                .sheet
                .items
                .iter()
                .filter(|d| {
                    d.kind == crate::output::Kind::Centreline
                        && mark.view.as_ref().is_some_and(|v| {
                            owners.iter().any(|id| d.name == format!("ink:{v}:{id}"))
                        })
                })
                .flat_map(|d| d.lines.iter().copied())
                .collect();
            let lines = centreline_witnesses(&item.lines, &item.heads, &centres)?;
            frame.and_then(|f| ink_span(a, f, &lines))
        });
        if let Some((axis, p, q)) = span {
            mark.claims.push(crate::output::Claim {
                feature: span_claim(axis, p, q),
                parameter: "station.distance".into(),
            });
        }
    }
}

/// Feature axes remain distinct even when they share a coordinate in a projection.
pub(crate) fn node_feature(a: &Analysis, id: &str, k: usize) -> Option<String> {
    if let Some(p) = pattern(a, id)
        && p.members.iter().all(|id| node_feature(a, id, k).is_some())
    {
        return Some(id.into());
    }
    if let Some(f) = a
        .features
        .iter()
        .find(|f| f.id == id && f.kind == "rectangular_through_notch")
    {
        let n = super::rectangular_notches::read(a, f).ok()?;
        return (k != n.axis).then(|| id.into());
    }
    if let Some(f) = a.features.iter().find(|f| f.id == id && f.kind == "slot") {
        return ["location_slot.length", "location_slot_across.length"]
            .iter()
            .any(|role| super::redundancy::span(a, &f.id, role).is_some_and(|span| span.axis == k))
            .then(|| id.into());
    }
    a.features
        .iter()
        .find(|f| {
            f.id == id
                && matches!(
                    f.kind.as_str(),
                    "hole" | "boss" | "polygonal_boss" | "oriented_slot"
                )
                && f.fingerprint.axis.is_some_and(|d| d[k].abs() < EPS)
        })
        .map(|f| f.id.clone())
}

pub(crate) fn pattern<'a>(
    a: &'a Analysis,
    id: &str,
) -> Option<std::borrow::Cow<'a, crate::bridge::HolePattern>> {
    if let Some(p) = a
        .recognition
        .as_ref()
        .and_then(|r| r.patterns.iter().find(|p| p.id == id))
    {
        return Some(std::borrow::Cow::Borrowed(p));
    }
    let f = a
        .features
        .iter()
        .find(|f| f.id == id && f.kind == "slot_pattern")?;
    let members = f
        .parameters
        .get("slots")?
        .as_array()?
        .iter()
        .filter_map(|s| s.as_str().map(str::to_string))
        .collect();
    Some(std::borrow::Cow::Owned(crate::bridge::HolePattern {
        id: f.id.clone(),
        arrangement: if f.parameters.contains_key("col_pitch") {
            "grid"
        } else {
            "linear"
        }
        .into(),
        members,
        faces: f.faces.clone(),
        parameters: f.parameters.clone(),
    }))
}

pub(crate) fn station(a: &Analysis, id: &str, k: usize) -> Option<f64> {
    if let Some(p) = pattern(a, id) {
        let points: Option<Vec<_>> = p.members.iter().map(|id| station(a, id, k)).collect();
        let points = points?;
        return (!points.is_empty()).then(|| points.iter().sum::<f64>() / points.len() as f64);
    }
    if let Some(f) = a.features.iter().find(|f| f.id == id) {
        if f.kind == "hole"
            && let Ok(h) = crate::bridge::holes::Hole::read(f, &crate::bridge::holes::frame(a))
        {
            return Some(h.at[k]);
        }
        if f.kind == "rectangular_through_notch" {
            return Some(super::rectangular_notches::read(a, f).ok()?.locator()[k]);
        }
        if f.kind == "slot" {
            let p = super::slots::centre(f).ok()?;
            return Some(crate::bridge::holes::to_part(&crate::bridge::holes::frame(a), p)[k]);
        }
        if let Some(v) = a.axis_station(id, k) {
            return Some(v);
        }
        return Some(f.fingerprint.position[k]);
    }
    a.scheme_station(id, k).ok().map(|p| p[k])
}

/// A cylindrical datum establishes its own axis, even though it cannot be a
/// planar origin for ± dimensions. Only features coaxial in both radial axes qualify.
pub(crate) fn datum_axis_owner(a: &Analysis, feature: &str, k: usize) -> Option<String> {
    let f = a.features.iter().find(|f| f.id == feature)?;
    let direction = f.fingerprint.axis?;
    let axis = direction.iter().position(|v| v.abs() > 1. - EPS)?;
    if axis == k {
        return None;
    }
    let Some(Reading::Read(report)) = &a.pmi else {
        return None;
    };
    let radial: Vec<_> = (0..3).filter(|v| *v != axis).collect();
    let record = report
        .records
        .iter()
        .filter(|r| r.category == Category::Datum)
        .find(|r| {
            r.faces.iter().any(|&i| {
                a.face_attachments.get(i).is_some_and(|face| {
                    face.surface.as_deref() == Some("cylinder")
                        && face.exact_axis == Some(axis)
                        && face.support.len() == 2
                        && radial.iter().enumerate().all(|(j, &k)| {
                            station(a, feature, k)
                                .is_some_and(|v| (v - face.support[j]).abs() < EPS)
                        })
                })
            })
        })?;
    Some(record.source_id.clone())
}

/// A completely located pattern also fixes its centre, without an extra centre ordinate.
pub(crate) fn proof(
    a: &Analysis,
    edges: &[Edge],
    roots: &[f64],
    axis: usize,
    at: f64,
    node: Option<&str>,
) -> Option<Vec<usize>> {
    if node.is_some_and(|feature| datum_axis_owner(a, feature, axis).is_some()) {
        return Some(Vec::new());
    }
    if let Some(path) = route(edges, roots, axis, at, node, &BTreeSet::new()) {
        return Some(path);
    }
    let p = pattern(a, node?)?;
    let mut path = Vec::new();
    for id in &p.members {
        path.extend(route(
            edges,
            roots,
            axis,
            station(a, id, axis)?,
            Some(id),
            &BTreeSet::new(),
        )?);
    }
    Some(path)
}

/// A coaxial feature may use the same printed route, without changing planner precedence.
pub(crate) fn drawn_proof(
    a: &Analysis,
    edges: &[Edge],
    roots: &[f64],
    axis: usize,
    at: f64,
    node: Option<&str>,
) -> Option<Vec<usize>> {
    if let Some(path) = proof(a, edges, roots, axis, at, node) {
        return Some(path);
    }
    // Coaxial cylinders share both radial coordinates. Equal coordinates on
    // just one axis are not a geometric relation between unrelated features.
    if let Some(f) = node.and_then(|id| a.features.iter().find(|f| f.id == id))
        && matches!(f.kind.as_str(), "hole" | "boss")
        && let Some(d) = f.fingerprint.axis
        && d[axis].abs() < EPS
    {
        for other in a
            .features
            .iter()
            .filter(|g| g.id != f.id && matches!(g.kind.as_str(), "hole" | "boss"))
        {
            let Some(e) = other.fingerprint.axis else {
                continue;
            };
            if ((0..3).map(|k| d[k] * e[k]).sum::<f64>().abs() - 1.).abs() > EPS {
                continue;
            }
            let delta: [f64; 3] = std::array::from_fn(|k| {
                station(a, &other.id, k).unwrap_or(other.fingerprint.position[k])
                    - station(a, &f.id, k).unwrap_or(f.fingerprint.position[k])
            });
            let axial = (0..3).map(|k| delta[k] * d[k]).sum::<f64>();
            if (0..3).any(|k| (delta[k] - axial * d[k]).abs() > EPS) {
                continue;
            }
            if let Some(path) = route(
                edges,
                roots,
                axis,
                station(a, &other.id, axis)?,
                Some(&other.id),
                &BTreeSet::new(),
            ) {
                return Some(path);
            }
        }
    }
    None
}

fn pitch_states(a: &Analysis, p: &crate::bridge::HolePattern, k: usize, text: &str) -> bool {
    let Some(mut xs): Option<Vec<_>> = p.members.iter().map(|id| station(a, id, k)).collect()
    else {
        return false;
    };
    xs.sort_by(f64::total_cmp);
    xs.dedup_by(|x, y| (*x - *y).abs() < EPS);
    let (count, body) = text.split_once('×').map_or((Some(1), text), |(n, body)| {
        (n.trim().parse::<usize>().ok(), body.trim())
    });
    xs.len() >= 2
        && count == Some(xs.len() - 1)
        && states_mm(body, xs[1] - xs[0])
        && xs
            .windows(2)
            .all(|q| ((q[1] - q[0]) - (xs[1] - xs[0])).abs() < super::redundancy::TOL)
}

fn slot_end_witness(
    a: &Analysis,
    r: &crate::pmi::Record,
    f: &crate::bridge::Feature,
    k: usize,
    at: f64,
) -> bool {
    let Some(span) = super::redundancy::span(a, &f.id, "location_slot.length") else {
        return false;
    };
    if span.axis != k || (span.hi - at).abs() > EPS || f.faces.len() < 2 {
        return false;
    }
    // Drawing may sort the ends. Recover the authored group by station.
    let Some((side, _)) = r.geometry.as_ref().and_then(|g| {
        g.stations
            .iter()
            .enumerate()
            .find(|(_, p)| (p[k] - at).abs() <= EPS)
    }) else {
        return false;
    };
    let Some(group) = r.face_groups.get(side).filter(|g| !g.is_empty()) else {
        return false;
    };
    if !group.iter().all(|&i| {
        a.face_attachments
            .get(i)
            .is_some_and(|face| face.surface.as_deref() == Some("plane") && face.axis == Some(k))
    }) {
        return false;
    }
    let mut bounds = [f64::INFINITY; 6];
    bounds[3..].fill(f64::NEG_INFINITY);
    let mut common = [[f64::NEG_INFINITY; 3], [f64::INFINITY; 3]];
    for &i in &f.faces {
        let Some(wall) = a.face_attachments.get(i) else {
            return false;
        };
        let Some(wb) = wall.bounds else {
            return false;
        };
        if wall.surface.as_deref() != Some("plane")
            || wall.axis.is_none_or(|axis| axis == k)
            || (wb[k] - at).abs().min((wb[k + 3] - at).abs()) > EPS
        {
            return false;
        }
        for j in 0..3 {
            bounds[j] = bounds[j].min(wb[j]);
            bounds[j + 3] = bounds[j + 3].max(wb[j + 3]);
            if j != k && Some(j) != wall.axis {
                common[0][j] = common[0][j].max(wb[j]);
                common[1][j] = common[1][j].min(wb[j + 3]);
            }
        }
    }
    // Use an actual trimmed-face site, not a potentially hollow box centre.
    group
        .iter()
        .filter_map(|&i| a.face_attachments.get(i))
        .flat_map(|face| &face.sites)
        .any(|p| {
            (p[k] - at).abs() <= EPS
                && (0..3).all(|j| {
                    bounds[j] - EPS <= p[j]
                        && p[j] <= bounds[j + 3] + EPS
                        && common[0][j] - EPS <= p[j]
                        && p[j] <= common[1][j] + EPS
                })
        })
}

pub(crate) fn identify(a: &Analysis, mark: &Mark, spans: Vec<Edge>) -> Vec<Edge> {
    identify_impl(a, mark, spans, false)
}

fn identify_impl(a: &Analysis, mark: &Mark, spans: Vec<Edge>, authored: bool) -> Vec<Edge> {
    if spans.is_empty() {
        return Vec::new();
    }
    let report = match &a.pmi {
        Some(Reading::Read(r)) if mark.claims.iter().any(|c| c.feature.starts_with("pmi:")) => {
            Some((r, r.identities()))
        }
        _ => None,
    };
    let mut out = Vec::new();
    if report.is_some() {
        // A PMI carrier may now state a recognised uniform pitch as well as its
        // authored face pair. Keep the pitch's member constraints when sharing
        // its ink across views; they are not extra authored PMI references.
        let claims: Vec<_> = mark
            .claims
            .iter()
            .filter(|c| {
                !c.feature.starts_with("pmi:") && c.parameter.starts_with("grid_pitch.length.")
            })
            .cloned()
            .collect();
        if !claims.is_empty() {
            let mut pitch = mark.clone();
            pitch.claims = claims;
            out.extend(identify_impl(a, &pitch, spans.clone(), authored));
        }
    }

    for span in spans {
        if authored
            && let Some(c) = mark
                .claims
                .iter()
                .find(|c| c.feature.starts_with("geometry:"))
        {
            if let Some((k, p, q)) = authored_span(a, mark, c)
                && k == span.axis
                && (p.min(q) - span.a.min(span.b)).abs() < EPS
                && (p.max(q) - span.a.max(span.b)).abs() < EPS
            {
                let refs: Vec<_> = c
                    .feature
                    .trim_start_matches("geometry:")
                    .split('|')
                    .collect();
                let node = |side: usize| {
                    refs.get(side)
                        .and_then(|r| r.strip_prefix("axis:"))
                        .and_then(|id| node_feature(a, id, k))
                };
                let mut e = span;
                if (p - e.a).abs() < EPS {
                    e.a_feature = node(0);
                    e.b_feature = node(1);
                } else {
                    e.a_feature = node(1);
                    e.b_feature = node(0);
                }
                out.push(e);
            }
            continue;
        }
        if let Some(c) = mark
            .claims
            .iter()
            .find(|c| c.feature.starts_with("scheme:"))
        {
            if let Some(key) = c.feature.strip_prefix("scheme:")
                && let [_, _, from, to] = key.split('|').collect::<Vec<_>>().as_slice()
                && let Some((style, axis)) = c.parameter.split_once('.')
                && AXES[span.axis] == axis
                && let (Ok(p), Ok(q)) = (
                    a.scheme_station(from, span.axis),
                    a.scheme_station(to, span.axis),
                )
                && mark
                    .text
                    .split_whitespace()
                    .next()
                    .and_then(|s| s.parse::<f64>().ok())
                    .is_some_and(|n| {
                        let value = if style == "ordinate" {
                            q[span.axis] - p[span.axis]
                        } else {
                            (q[span.axis] - p[span.axis]).abs()
                        };
                        n.signum() == value.signum() && states_mm(&mark.text, value.abs())
                    })
            {
                let node = |key: &str| {
                    node_feature(a, key.strip_prefix("axis:").unwrap_or(key), span.axis)
                };
                let mut e = span.clone();
                if (p[span.axis] - span.a).abs() < super::redundancy::TOL
                    && (q[span.axis] - span.b).abs() < super::redundancy::TOL
                {
                    e.a_feature = node(from);
                    e.b_feature = node(to);
                    out.push(e);
                } else if (q[span.axis] - span.a).abs() < super::redundancy::TOL
                    && (p[span.axis] - span.b).abs() < super::redundancy::TOL
                {
                    e.a_feature = node(to);
                    e.b_feature = node(from);
                    out.push(e);
                }
            }
            continue;
        }
        let sources: Vec<_> = report
            .as_ref()
            .into_iter()
            .flat_map(|(report, ids)| {
                report.records.iter().filter(move |r| {
                    r.category == Category::Dimension
                        && ids
                            .id(&r.source_id)
                            .ok()
                            .is_some_and(|id| mark.claims.iter().any(|c| c.feature == id))
                })
            })
            .collect();
        if !sources.is_empty() {
            for r in sources {
                let mut e = span.clone();
                let feature = |side: usize, at: f64| {
                    let candidates: Vec<_> = a
                        .features
                        .iter()
                        .filter(|f| {
                            f.kind != "hole_pattern"
                                && node_feature(a, &f.id, e.axis).is_some()
                                && r.faces.iter().any(|i| f.faces.contains(i))
                                && station(a, &f.id, e.axis)
                                    .is_some_and(|p| (p - at).abs() < super::redundancy::TOL)
                        })
                        .collect();
                    if candidates.len() == 1 {
                        return Some(candidates[0].id.clone());
                    }
                    if let Some(direct) = r.face_groups.get(side).and_then(|faces| {
                        candidates
                            .iter()
                            .find(|f| faces.iter().any(|i| f.faces.contains(i)))
                    }) {
                        return Some(direct.id.clone());
                    }
                    // Prefer native face ownership. A planar end omitted from
                    // the slot's side-wall support needs its own cap proof.
                    let slots: Vec<_> = a
                        .features
                        .iter()
                        .filter(|f| f.kind == "slot" && slot_end_witness(a, r, f, e.axis, at))
                        .collect();
                    (slots.len() == 1).then(|| slots[0].id.clone())
                };
                e.a_feature = feature(0, e.a);
                e.b_feature = feature(1, e.b);
                // A proven compound bore endpoint can also locate an included
                // pattern's centre along the measured normal. Require its entire
                // native support and that centre station: the transverse witness
                // can be a real member away from an empty pattern centre. A
                // coincident bore without that complete support is not an alias.
                if let Some(g) = &r.geometry
                    && g.reasons.is_empty()
                    && g.sites.len() == 2
                    && g.stations.len() == 2
                {
                    for side in 0..2 {
                        let Some(group) = r.face_groups.get(side) else {
                            continue;
                        };
                        let Some(axis) = group
                            .first()
                            .and_then(|&f| a.face_attachments.get(f).and_then(|f| f.exact_axis))
                        else {
                            continue;
                        };
                        if group.iter().any(|&f| {
                            a.face_attachments.get(f).is_none_or(|f| {
                                f.surface.as_deref() != Some("cylinder")
                                    || f.exact_axis != Some(axis)
                            })
                        }) {
                            continue;
                        }
                        for p in a.recognition.iter().flat_map(|r| &r.patterns) {
                            if p.faces.is_empty()
                                || !p.faces.iter().all(|f| group.contains(f))
                                || node_feature(a, &p.id, e.axis).is_none()
                                || e.axis == axis
                                || station(a, &p.id, e.axis)
                                    .is_none_or(|at| (at - g.stations[side][e.axis]).abs() > EPS)
                            {
                                continue;
                            }
                            let mut alias = e.clone();
                            if (g.stations[side][e.axis] - e.a).abs() <= EPS {
                                alias.a_feature = Some(p.id.clone());
                            } else if (g.stations[side][e.axis] - e.b).abs() <= EPS {
                                alias.b_feature = Some(p.id.clone());
                            } else {
                                continue;
                            }
                            out.push(alias);
                        }
                    }
                }
                // A cap's virtual slot identity adds evidence; it must not
                // replace the physical plane used by other source locations.
                // Retain the other endpoint's native identity, including bores.
                let mut physical = e.clone();
                for (node, at) in [
                    (&mut physical.a_feature, e.a),
                    (&mut physical.b_feature, e.b),
                ] {
                    if node
                        .as_ref()
                        .and_then(|id| a.features.iter().find(|f| f.id == *id))
                        .is_some_and(|f| f.kind == "slot" && slot_end_witness(a, r, f, e.axis, at))
                    {
                        *node = None;
                    }
                }
                if physical.a_feature != e.a_feature || physical.b_feature != e.b_feature {
                    out.push(physical);
                }
                out.push(e);
            }
            continue;
        }
        let pitch = mark
            .claims
            .iter()
            .find(|c| c.parameter.contains("pitch.length"));
        let mut expanded_pitch = false;
        for c in mark
            .claims
            .iter()
            .filter(|c| c.parameter.contains("pitch.length"))
        {
            let Some(p) = pattern(a, &c.feature) else {
                continue;
            };
            if !pitch_states(a, &p, span.axis, &mark.text) {
                continue;
            }
            let centre = station(a, &p.id, span.axis);
            if let Some(centre) = centre {
                for id in &p.members {
                    if let Some(at) = station(a, id, span.axis) {
                        let mut e = span.clone();
                        e.a = centre;
                        e.b = at;
                        e.a_feature = Some(p.id.clone());
                        e.b_feature = Some(id.clone());
                        out.push(e);
                    }
                }
                // A line of holes has one common transverse station on its other axis.
                for k in 0..3 {
                    if k == span.axis || node_feature(a, &p.id, k).is_none() {
                        continue;
                    }
                    let Some(c) = station(a, &p.id, k) else {
                        continue;
                    };
                    if p.members
                        .iter()
                        .all(|id| station(a, id, k).is_some_and(|v| (v - c).abs() < EPS))
                    {
                        for id in &p.members {
                            let mut e = span.clone();
                            e.axis = k;
                            e.a = c;
                            e.b = c;
                            e.a_feature = Some(p.id.clone());
                            e.b_feature = Some(id.clone());
                            out.push(e);
                        }
                    }
                }
                expanded_pitch = true;
            }
        }
        if expanded_pitch {
            continue;
        }
        let location = mark
            .claims
            .iter()
            .any(|c| c.parameter.starts_with("location"));
        let mut members = Vec::new();
        for c in &mark.claims {
            if !(c.parameter.starts_with("location") || c.parameter.contains("pitch.length")) {
                continue;
            }
            if node_feature(a, &c.feature, span.axis).is_some() {
                members.push(c.feature.clone());
            }
            if let Some(p) = pattern(a, &c.feature) {
                members.extend(p.members.iter().cloned());
                members.push(p.id.clone());
            }
        }
        members.sort();
        members.dedup();
        let at = |id: &String| {
            let mut p = a
                .features
                .iter()
                .find(|f| f.id == *id)
                .map_or([0.; 3], |f| f.fingerprint.position);
            p[span.axis] = station(a, id, span.axis)?;
            Some(p)
        };
        let left: Vec<_> = members
            .iter()
            .filter(|id| {
                at(id).is_some_and(|p| (p[span.axis] - span.a).abs() < super::redundancy::TOL)
            })
            .collect();
        let right: Vec<_> = members
            .iter()
            .filter(|id| {
                at(id).is_some_and(|p| (p[span.axis] - span.b).abs() < super::redundancy::TOL)
            })
            .collect();
        if pitch.is_some() && !left.is_empty() && !right.is_empty() {
            for l in &left {
                for r in &right {
                    // Invariant: left was filtered with this same at(id).is_some_and predicate.
                    let p = at(l).unwrap();
                    // Invariant: right was filtered with this same at(id).is_some_and predicate.
                    let q = at(r).unwrap();
                    let normal = a
                        .features
                        .iter()
                        .find(|f| f.id == **l)
                        .and_then(|f| f.fingerprint.axis)
                        .and_then(crate::bridge::holes::principal);
                    if (0..3)
                        .any(|k| k != span.axis && Some(k) != normal && (p[k] - q[k]).abs() > EPS)
                    {
                        continue;
                    }
                    let mut e = span.clone();
                    e.a_feature = Some((**l).clone());
                    e.b_feature = Some((**r).clone());
                    out.push(e);
                }
            }
        } else if location
            && right.is_empty()
            && members.len() == 1
            && mark
                .claims
                .iter()
                .any(|c| c.parameter.starts_with("location_slot."))
        {
            let mut e = span;
            e.b_feature = Some(members[0].clone());
            out.push(e);
        } else if location && !right.is_empty() {
            for r in right {
                let mut e = span.clone();
                e.b_feature = Some(r.clone());
                out.push(e);
            }
        } else {
            out.push(span);
        }
    }
    out
}

/// Requirements come from the part, not the engine inventory. Cylindrical features need their
/// transverse axes located; other prismatic features use their recognised locating station.
pub(crate) fn targets(a: &Analysis) -> Vec<(String, usize, f64)> {
    let mut out = Vec::new();
    for f in &a.features {
        match f.kind.as_str() {
            "hole" | "boss" | "polygonal_boss" | "oriented_slot" => {
                for k in (0..3).filter(|&k| f.fingerprint.axis.is_some_and(|d| d[k].abs() < EPS)) {
                    let at = station(a, &f.id, k).unwrap_or(f.fingerprint.position[k]);
                    out.push((f.id.clone(), k, at));
                }
            }
            "slot" => {
                for role in ["location_slot.length", "location_slot_across.length"] {
                    if let Some(span) = super::redundancy::span(a, &f.id, role) {
                        out.push((
                            f.id.clone(),
                            span.axis,
                            station(a, &f.id, span.axis).unwrap_or(span.hi),
                        ));
                    }
                }
            }
            "rectangular_through_notch" => {
                if let Ok(n) = super::rectangular_notches::read(a, f) {
                    for k in [n.wide, n.deep] {
                        out.push((f.id.clone(), k, n.locator()[k]));
                    }
                }
            }
            "turned_step" => {
                if let Ok(b) = super::turned::band(&crate::bridge::holes::frame(a), f) {
                    out.push((f.id.clone(), b.axis, b.lo));
                }
            }
            kind if kind.contains("step") => {
                for (i, face) in f
                    .faces
                    .iter()
                    .filter_map(|&i| a.face_attachments.get(i).map(|f| (i, f)))
                {
                    if face.surface.as_deref() == Some("plane")
                        && let Some(k) = face.axis
                        && let Some(p) = face.sites.first()
                    {
                        out.push((format!("face:{i}"), k, p[k]));
                    }
                }
            }
            _ => {}
        }
    }
    for (f, p) in super::pockets::placed(a) {
        let Ok(p) = p else { continue };
        if p.edge_anchored {
            continue;
        }
        for (role, distance) in super::pockets::locations(&p, a.envelope[0]) {
            if let Some(k) = AXES.iter().position(|axis| role.ends_with(axis)) {
                out.push((f.id.clone(), k, a.envelope[0][k] + distance));
            }
        }
    }
    for n in super::notch_evidence::requirements(a) {
        for k in [n.width_axis, n.depth_axis] {
            out.push((n.id.clone(), k, n.mouth[k]));
        }
    }
    // Invariant: The collected positions passed finite coordinate parsing.
    out.sort_by(|p, q| p.partial_cmp(q).unwrap());
    out.dedup_by(|p, q| p.0 == q.0 && p.1 == q.1 && (p.2 - q.2).abs() < EPS);
    out
}

pub fn coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let edges = drawn_edges(a, marks);
    let roots = drawing_roots(a, marks);
    let mut issues = Vec::new();
    for (feature, k, at) in targets(a) {
        if datum_axis_owner(a, &feature, k).is_some() {
            continue;
        }
        let parameter = format!("location.length.{}", AXES[k]);
        let first = drawn_proof(
            a,
            &edges,
            &roots[k],
            k,
            at,
            node_feature(a, &feature, k).as_deref(),
        );
        let (code, involved) = match first {
            None => {
                let node = node_feature(a, &feature, k);
                let involved: Vec<_> = edges
                    .iter()
                    .enumerate()
                    .filter(|(_, e)| {
                        e.axis == k
                            && ((e.a_feature == node && (node.is_some() || (e.a - at).abs() < EPS))
                                || (e.b_feature == node
                                    && (node.is_some() || (e.b - at).abs() < EPS)))
                    })
                    .map(|(i, _)| i)
                    .collect();
                ("feature_not_located", involved)
            }
            Some(first) => {
                if first.is_empty() {
                    continue;
                }
                let owners: BTreeSet<_> = first.iter().map(|&i| edges[i].owner.as_str()).collect();
                let blocked = edges
                    .iter()
                    .enumerate()
                    .filter(|(_, e)| owners.contains(e.owner.as_str()))
                    .map(|(i, _)| i)
                    .collect();
                let Some(second) = route(
                    &edges,
                    &roots[k],
                    k,
                    at,
                    node_feature(a, &feature, k).as_deref(),
                    &blocked,
                ) else {
                    continue;
                };
                (
                    "feature_located_twice",
                    first.into_iter().chain(second).collect(),
                )
            }
        };
        let mut names: Vec<_> = involved.iter().map(|&i| edges[i].owner.clone()).collect();
        names.sort();
        names.dedup();
        let mut issue = uncovered(
            a,
            code,
            &feature,
            &parameter,
            format!(
                "{feature} on {}: {}{}.",
                AXES[k],
                if code == "feature_not_located" {
                    "no drawn dimension chain reaches an origin"
                } else {
                    "independent drawn dimension chains reach an origin"
                },
                if names.is_empty() {
                    String::new()
                } else {
                    format!(" ({})", names.join(", "))
                }
            ),
        );
        issue.related = names;
        issues.push(issue);
    }
    issues
}

/// Missing sizes are checked from physical feature claims or the source's attached faces.
pub fn sizes(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let report = match &a.pmi {
        Some(Reading::Read(r)) => Some((r, r.identities())),
        _ => None,
    };
    let mut issues = Vec::new();
    for f in a.features.iter().filter(|f| {
        matches!(
            f.kind.as_str(),
            "hole" | "slot" | "boss" | "pocket" | "turned_step"
        )
    }) {
        let sized = marks.iter().any(|m| {
            m.claims.iter().any(|c| {
                c.feature == f.id
                    && !c.parameter.starts_with("location")
                    && !c.parameter.starts_with("seat_location.")
                    && !c.parameter.starts_with("step_position.")
                    && !c.parameter.starts_with("grouping")
                    && c.parameter != "thread"
            })
        }) || report.as_ref().is_some_and(|(r, ids)| {
            r.records.iter().any(|r| {
                ((r.category == Category::ManufacturingRequirement && r.kind.ends_with("thread"))
                    || (r.category == Category::Dimension
                        && matches!(
                            r.kind.as_str(),
                            "diameter" | "radius" | "thickness" | "linear"
                        )))
                    && !r.faces.is_empty()
                    && r.faces.iter().all(|i| f.faces.contains(i))
                    && ids.id(&r.source_id).ok().is_some_and(|id| {
                        marks
                            .iter()
                            .any(|m| m.claims.iter().any(|c| c.feature == id))
                    })
            })
        });
        if !sized {
            issues.push(uncovered(
                a,
                "feature_not_sized",
                &f.id,
                "size",
                format!("{} has no drawn size.", f.id),
            ));
        }
    }
    issues
}

/// Canonical legacy coverage roles derived from the physical network. These are arithmetic
/// evidence for the older family checks; they are never extra printed dimensions.
pub(crate) fn evidence(a: &Analysis, marks: &[Mark]) -> Vec<Mark> {
    let edges = drawn_edges(a, marks);
    let roots = drawing_roots(a, marks);
    let mut extra = Vec::new();
    let pockets = super::pockets::placed(a);
    for (feature, k, at) in targets(a) {
        if drawn_proof(
            a,
            &edges,
            &roots[k],
            k,
            at,
            node_feature(a, &feature, k).as_deref(),
        )
        .is_none()
        {
            continue;
        }
        let mut claims = vec![crate::output::Claim {
            feature: feature.clone(),
            parameter: format!("location.length.{}", AXES[k]),
        }];
        for role in ["location_slot.length", "location_slot_across.length"] {
            if super::redundancy::span(a, &feature, role).is_some_and(|s| s.axis == k) {
                claims.push(crate::output::Claim {
                    feature: feature.clone(),
                    parameter: role.into(),
                });
            }
        }
        for (f, read) in &pockets {
            if f.id != feature {
                continue;
            }
            if let Ok(p) = read {
                for (role, _) in super::pockets::locations(p, a.envelope[0]) {
                    if role.ends_with(AXES[k]) {
                        claims.push(crate::output::Claim {
                            feature: feature.clone(),
                            parameter: role,
                        });
                    }
                }
            }
        }
        extra.push(Mark {
            name: format!("location_network:{feature}/{}", AXES[k]),
            view: None,
            text: format!("{}", (at - a.envelope[0][k]).abs()),
            claims,
        });
    }
    for (f, _) in &pockets {
        for role in ["pocket_width.length", "pocket_length.length"] {
            let Some(s) = super::redundancy::span(a, &f.id, role) else {
                continue;
            };
            // A size is stated between its two physical faces. A feature axis
            // merely sharing one face's coordinate is not a witness to that face.
            let excluded = edges
                .iter()
                .enumerate()
                .filter(|(_, e)| e.a_feature.is_some() || e.b_feature.is_some())
                .map(|(i, _)| i)
                .collect();
            if route(&edges, &[s.lo], s.axis, s.hi, None, &excluded).is_some() {
                extra.push(Mark {
                    name: format!("location_network:{}/{role}", f.id),
                    view: None,
                    text: super::parameters::fmt(s.hi - s.lo),
                    claims: vec![crate::output::Claim {
                        feature: f.id.clone(),
                        parameter: role.into(),
                    }],
                });
            }
        }
    }
    extra
}

/// How far apart two raw endpoints may be and still be one span, mm: geometric identity, not
/// the label's rounding (quiddity's 0.01 mm record rounding is already undone where it is read),
/// but above a model's coordinate noise: ctc01's hexagonal boss centre, averaged from its
/// corners, lies at 224.999973 where the holes on the same line lie at 225.
pub(crate) const SAME_SPAN: f64 = 1e-4;

/// A boss whose centre stands on another feature's location line (a hole's or pattern's
/// `location.length.<axis>`, within [`SAME_SPAN`], which the planner shares: lint reads no
/// plan) has its own location omitted as conveyed by that line (`dims::holes::consolidate`, ARCH0009-6):
/// ctc01's hexagonal boss shares its holes' `400` and `225`. A mark claiming such a location on
/// the boss's axis claims the boss's too, for every rule; the location network still lands its
/// witness on the boss's centre from the ink before the boss counts as located.
pub(crate) fn conveyed_boss_locations(a: &Analysis, marks: &mut [Mark]) {
    let bosses: Vec<(&str, usize, f64)> = a
        .features
        .iter()
        .filter(|f| matches!(f.kind.as_str(), "boss" | "polygonal_boss"))
        .flat_map(|f| {
            (0..3).filter_map(move |k| {
                node_feature(a, &f.id, k)?;
                Some((f.id.as_str(), k, station(a, &f.id, k)?))
            })
        })
        .collect();
    if bosses.is_empty() {
        return;
    }
    for m in marks {
        // A hole table row's coordinate locates its own hole and has no witness line for a
        // boss to share: STC-07's bosses, each on two rows' x and y, read as located twice.
        if m.name.starts_with("hole_schedule_") || m.name.starts_with("hole_table_plan/") {
            continue;
        }
        let mut conveyed = Vec::new();
        for &(id, k, at) in &bosses {
            let parameter = format!("location.length.{}", AXES[k]);
            if m.claims
                .iter()
                .any(|c| c.feature == id && c.parameter == parameter)
            {
                continue;
            }
            if m.claims.iter().any(|c| {
                c.feature != id
                    && c.parameter == parameter
                    && station(a, &c.feature, k).is_some_and(|v| (v - at).abs() <= SAME_SPAN)
            }) {
                conveyed.push(crate::output::Claim {
                    feature: id.into(),
                    parameter,
                });
            }
        }
        m.claims.extend(conveyed);
    }
}

/// Finishes, threads and labels do not introduce dimensional station requirements.
pub(crate) fn has_station_requirements(a: &Analysis) -> bool {
    matches!(&a.pmi, Some(Reading::Read(r)) if r.records.iter().any(|r| {
        matches!(r.category, Category::Datum | Category::GeometricTolerance)
            || (r.category == Category::Dimension && r.kind != "common_label")
    }))
}

/// Source-PMI audits additionally check every station and independent duplicate
/// routes. Other drawings use the same graph over the existing family/location
/// requirement inventory, retaining unsupported (for example inclined) axes.
pub(crate) fn active(a: &Analysis, marks: &[Mark]) -> bool {
    has_station_requirements(a)
        || marks.iter().flat_map(|m| &m.claims).any(|c| {
            c.feature.starts_with("station_origin:") || c.feature.starts_with("station_root:")
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn edge(a: f64, b: f64, name: &str) -> Edge {
        Edge {
            axis: 0,
            a,
            b,
            owner: name.into(),
            a_feature: None,
            b_feature: None,
        }
    }
    #[test]
    fn explicit_imperial_pmi_locates_in_the_parts_millimetre_frame() {
        assert!(states_mm("0.30 ±0.02 INCH", 7.62));
        assert!(!states_mm("0.30 INCH", 0.30));
        assert!(states_mm("7.62", 7.62));
    }
    #[test]
    fn rounded_recognition_stations_still_reach_the_physical_origin() {
        assert!(
            route(
                &[edge(-0.000369, 5.5, "baseline")],
                &[0.],
                0,
                5.5,
                None,
                &BTreeSet::new()
            )
            .is_some()
        );
    }
    #[test]
    fn a_feature_axis_on_the_origin_needs_no_zero_dimension() {
        assert_eq!(
            route(&[], &[0.], 0, 0., Some("hole"), &BTreeSet::new()),
            Some(vec![])
        );
        assert!(route(&[], &[0.], 0, 0.005, Some("hole"), &BTreeSet::new()).is_none());
    }
    #[test]
    fn coincident_feature_axes_remain_distinct_nodes() {
        let mut a = edge(0., 50., "PMI A");
        a.b_feature = Some("hole A".into());
        let mut b = edge(0., 50., "PMI B");
        b.b_feature = Some("hole B".into());
        let edges = vec![a, b];
        assert!(route(&edges, &[0.], 0, 50., Some("hole A"), &BTreeSet::from([0])).is_none());
        assert!(route(&edges, &[0.], 0, 50., Some("hole B"), &BTreeSet::new()).is_some());
    }
    #[test]
    fn chains_require_an_origin_and_preserve_parallel_authored_constraints() {
        let edges = vec![
            edge(100., 95., "right PMI"),
            edge(95., 50., "PMI chain"),
            edge(0., 95., "left PMI"),
        ];
        let first = route(&edges, &[0., 100.], 0, 50., None, &BTreeSet::new()).unwrap();
        assert_eq!(first.len(), 2);
        assert!(route(&edges, &[0., 100.], 1, 50., None, &BTreeSet::new()).is_none());
        assert!(route(&edges, &[20.], 0, 50., None, &BTreeSet::new()).is_none());
        let first = route(&edges, &[0., 100.], 0, 95., None, &BTreeSet::new()).unwrap();
        let blocked = first.into_iter().collect();
        assert!(route(&edges, &[0., 100.], 0, 95., None, &blocked).is_some());
    }
    /// ctc01: a boss whose centre (2.7e-5 short, from its corners) stands on a hole's location
    /// line is claimed by a mark claiming that line, on that axis only; a hole 0.01 off is
    /// another station and conveys nothing.
    #[test]
    fn a_bosss_centre_on_a_holes_location_line_is_claimed_by_it() {
        let feature = |id: &str, kind: &str, at: [f64; 3]| {
            serde_json::json!({
                "id": id, "kind": kind, "faces": [], "parameters": {},
                "fingerprint": { "version": "test", "family": "test", "subtype": "test",
                                 "sizes": {}, "position": at, "axis": [0.0, 0.0, 1.0],
                                 "neighbourhood": [] },
            })
        };
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 9, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [800.0, 450.0, 50.0]],
            "features": [
                feature("f:polygonal_boss:1", "polygonal_boss", [400.0, 224.999973, 40.0]),
                feature("f:hole:1", "hole", [400.0, 225.0, 25.0]),
                feature("f:hole:2", "hole", [500.0, 225.01, 25.0]),
            ],
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        }))
        .unwrap();
        let claimed = |feature: &str, parameter: &str| -> Vec<String> {
            let mut marks = [Mark {
                name: "d".into(),
                view: Some("v:plan".into()),
                text: "225".into(),
                claims: vec![crate::output::Claim {
                    feature: feature.into(),
                    parameter: parameter.into(),
                }],
            }];
            conveyed_boss_locations(&a, &mut marks);
            marks[0].claims[1..]
                .iter()
                .map(|c| format!("{} {}", c.feature, c.parameter))
                .collect()
        };
        assert_eq!(
            claimed("f:hole:1", "location.length.y"),
            ["f:polygonal_boss:1 location.length.y"]
        );
        assert_eq!(
            claimed("f:hole:1", "location.length.x"),
            ["f:polygonal_boss:1 location.length.x"]
        );
        assert!(claimed("f:hole:2", "location.length.y").is_empty());
        assert!(claimed("f:hole:1", "hole_diameter.length").is_empty());
    }
    #[test]
    fn only_true_coaxial_features_share_a_printed_location_chain() {
        let feature = |id: &str, kind: &str, at: [f64; 3]| {
            serde_json::json!({
                "id": id, "kind": kind, "faces": [], "parameters": {},
                "fingerprint": {"version": "test", "family": "test", "subtype": "test",
                    "sizes": {}, "position": at, "axis": [0., 0., 1.], "neighbourhood": []}
            })
        };
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema": "draftwright.analysis/2",
            "binding": {"source_sha256": "", "part": "test", "face_count": 0, "loader": "test"},
            "envelope": [[0., 0., 0.], [100., 60., 20.]],
            "features": [feature("hole", "hole", [40., 22., 5.]),
                feature("boss", "boss", [40., 22., 15.]),
                feature("unrelated", "boss", [40., 32., 15.])],
            "iso_bounds": [0., 0., 0., 0.]
        }))
        .unwrap();
        let mut e = edge(0., 40., "printed hole location");
        e.b_feature = Some("hole".into());
        assert_eq!(
            drawn_proof(&a, &[e.clone()], &[0.], 0, 40., Some("boss")),
            Some(vec![0])
        );
        assert!(drawn_proof(&a, &[e], &[0.], 0, 40., Some("unrelated")).is_none());
        assert!(drawn_proof(&a, &[], &[0.], 0, 40., Some("boss")).is_none());
        assert!(drawn_proof(&a, &[], &[0.], 1, 22., Some("boss")).is_none());
    }

    #[test]
    fn direct_pitch_requires_its_drawn_centre_lines_and_arrowheads() {
        let lines = [
            [[2., 0.], [2., 0.]],
            [[2., 90.], [2., 90.]],
            [[0., 0.], [0., 90.]],
        ];
        let heads = [
            [[0., 0.], [-1., 3.]],
            [[1., 3.], [0., 0.]],
            [[0., 90.], [-1., 87.]],
            [[1., 87.], [0., 90.]],
        ];
        let centres = [[[-5., 0.], [5., 0.]], [[-5., 90.], [5., 90.]]];
        let ink = centreline_witnesses(&lines, &heads, &centres).unwrap();
        assert_eq!(crate::output::linear_ink(&ink).unwrap().span, [0., 90.]);
        assert!(centreline_witnesses(&lines, &[], &centres).is_none());
        assert!(centreline_witnesses(&lines, &heads, &centres[..1]).is_none());
        let mut wrong = centres;
        wrong[1] = [[-5., 89.], [5., 89.]];
        assert!(centreline_witnesses(&lines, &heads, &wrong).is_none());
        let mut invisible = lines;
        invisible[1] = [[10., 90.], [10., 90.]];
        assert!(centreline_witnesses(&invisible, &heads, &centres).is_none());
    }
}
