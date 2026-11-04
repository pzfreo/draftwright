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
fn printed_depth(text: &str, depth: f64) -> bool {
    if let Some((_, rest)) = text.split_once('↧') {
        return states_mm(rest.split_whitespace().next().unwrap_or(""), depth);
    }
    text.to_ascii_uppercase()
        .split_once("DEEP")
        .is_some_and(|(before, _)| {
            let term = before
                .rsplit('×')
                .next()
                .unwrap_or(before)
                .split('±')
                .next()
                .unwrap_or("")
                .split_whitespace()
                .next_back()
                .unwrap_or("");
            term.parse::<f64>().is_ok_and(|n| n.is_finite() && n >= 0.) && states_mm(term, depth)
        })
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
    /// A circular drawing datum explicitly witnessed at the first endpoint (lint only).
    pub a_datum: bool,
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
    walk(edges, axis, at, feature, excluded, |p, node| {
        // A feature on an origin plane needs no additional zero-length dimension.
        // Analytic feature axes use the tighter tolerance; rounded planes use TOL.
        !node.is_some_and(|id| id.starts_with("midplane:"))
            && roots.iter().any(|r| {
                (r - p).abs()
                    < if node.is_none() {
                        super::redundancy::TOL
                    } else {
                        EPS
                    }
            })
    })
}

fn walk(
    edges: &[Edge],
    axis: usize,
    at: f64,
    feature: Option<&str>,
    excluded: &BTreeSet<usize>,
    rooted: impl Fn(f64, Option<&str>) -> bool,
) -> Option<Vec<usize>> {
    let mut queue = vec![(at, feature.map(str::to_string), Vec::new())];
    let mut seen = vec![(at, feature.map(str::to_string))];
    let mut cursor = 0;
    while cursor < queue.len() {
        let (p, node, path) = queue[cursor].clone();
        cursor += 1;
        if rooted(p, node.as_deref()) {
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

/// The unique exterior circular datum, measured solely from Analysis geometry.
/// Shared with automatic origin selection; the audit never reads authoring or planning state.
pub(crate) fn round_origin(
    analysis: &Analysis,
    view: usize,
    k: usize,
) -> Option<(String, [f64; 3])> {
    use crate::bridge::holes as geometry;
    // Preserve the planner predicate's exact three-component record requirement.
    fn point(v: Option<&serde_json::Value>) -> Option<[f64; 3]> {
        let c: Vec<f64> = v?
            .as_array()?
            .iter()
            .map(serde_json::Value::as_f64)
            .collect::<Option<_>>()?;
        <[f64; 3]>::try_from(c).ok()
    }
    // Legacy recognition records round axis positions to 0.01 mm.
    const ON_AXIS: f64 = 0.01;
    let ink = analysis.edge_ink.as_ref()?;
    let lo = analysis.envelope[0][k];
    let flat = ink.planes.iter().any(|p| {
        p.normal[k].abs() > 1.0 - 1e-6
            && p.edges
                .iter()
                .filter_map(|&e| ink.edges.get(e)?.first())
                .any(|q| (q[k] - lo).abs() < 1e-4)
    });
    if flat {
        return None;
    }
    let (u, v) = match view {
        0 => (1, 2),
        1 => (0, 2),
        _ => (0, 1),
    };
    let frame = geometry::frame(analysis);
    let mut found: Option<(String, [f64; 3])> = None;
    for c in ink.curved.iter().filter_map(|c| {
        let y = c.cylinder.as_ref()?;
        (y.axis[view].abs() > 1.0 - 1e-6
            && (y.origin[k] - y.radius - lo).abs() < 1e-4
            && (c.bounds[0][k] - lo).abs() < 1e-4)
            .then_some(y)
    }) {
        let on = |at: [f64; 3]| {
            (at[u] - c.origin[u]).abs() < ON_AXIS && (at[v] - c.origin[v]).abs() < ON_AXIS
        };
        // A hole on the axis carries its centre mark there; else a boss standing on it.
        let owner = geometry::read(analysis)
            .into_iter()
            .filter_map(|(f, h)| h.ok().map(|h| (f, h)))
            .find(|(_, h)| h.direction[view].abs() > 1.0 - 1e-6 && on(h.at))
            .map(|(f, _)| f.id.clone())
            .or_else(|| {
                analysis
                    .features
                    .iter()
                    .filter(|f| f.kind == "boss")
                    .find(|f| {
                        let at = point(f.parameters.get("location"));
                        let d = point(f.parameters.get("axis"));
                        at.zip(d).is_some_and(|(at, d)| {
                            geometry::dir_to_part(&frame, d)[view].abs() > 1.0 - 1e-6
                                && on(geometry::to_part(&frame, at))
                        })
                    })
                    .map(|f| f.id.clone())
            })
            .or_else(|| {
                analysis
                    .features
                    .iter()
                    .filter(|f| f.kind == "turned_body")
                    .find(|f| {
                        super::parameters::place(&frame, f.parameters.get("axis_point"))
                            .zip(super::parameters::direction(
                                &frame,
                                f.parameters.get("axis"),
                            ))
                            .is_some_and(|(at, d)| d[view].abs() > 1.0 - 1e-6 && on(at))
                    })
                    .map(|f| f.id.clone())
            });
        let Some(owner) = owner else { continue };
        match &found {
            Some((_, at)) if (at[k] - c.origin[k]).abs() > ON_AXIS => return None, // two round sides
            Some(_) => {}
            None => found = Some((owner, c.origin)),
        }
    }
    found
}

/// The established round-outline origin is a drawing datum, not its box tangent (#223).
/// Only a printed distance whose own witnesses join that unique axis and the claimed
/// feature may use it; a matching number alone is insufficient.
fn round_axis_edges(a: &Analysis, marks: &[Mark]) -> Vec<Edge> {
    if matches!(&a.pmi, Some(Reading::Read(r)) if !r.records.is_empty()) {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut origins = std::collections::BTreeMap::new();
    for mark in marks.iter().filter(|m| {
        !m.text.starts_with('(')
            && !m.name.starts_with("location_network:")
            && !m.name.starts_with("derived:")
            && !m.claims.iter().any(|c| c.feature.starts_with("geometry:"))
    }) {
        let physical = mark
            .claims
            .iter()
            .any(|c| c.feature == "station_ink" || c.feature.starts_with("station_span:"));
        if !physical {
            continue;
        }
        let witnessed = edges(a, std::slice::from_ref(mark));
        for c in &mark.claims {
            let Some(k) = c
                .parameter
                .strip_prefix("location.length.")
                .and_then(|axis| AXES.iter().position(|v| *v == axis))
            else {
                continue;
            };
            let Some(f) = a
                .features
                .iter()
                .find(|f| f.id == c.feature && matches!(f.kind.as_str(), "hole" | "boss"))
            else {
                continue;
            };
            let Some(view) = f.fingerprint.axis.and_then(crate::bridge::holes::principal) else {
                continue;
            };
            if view == k {
                continue;
            }
            let Some((owner, origin)) = origins
                .entry((view, k))
                .or_insert_with(|| round_origin(a, view, k))
                .as_ref()
            else {
                continue;
            };
            let Some(at) = station(a, &f.id, k) else {
                continue;
            };
            if (at - origin[k]).abs() < EPS
                // A raw table/claim already stating the box coordinate is not an axis datum.
                || (!physical && states_mm(mark.text.split_whitespace().next().unwrap_or(""),
                    (at - a.envelope[0][k]).abs()))
                || !states_mm(
                    mark.text.split_whitespace().next().unwrap_or(""),
                    (at - origin[k]).abs(),
                )
                || (physical
                    && !witnessed.iter().any(|e| {
                        e.axis == k
                            && (e.a.min(e.b) - at.min(origin[k])).abs() < super::redundancy::TOL
                            && (e.a.max(e.b) - at.max(origin[k])).abs() < super::redundancy::TOL
                    }))
            {
                continue;
            }
            out.push(Edge {
                axis: k,
                a: origin[k],
                b: at,
                owner: mark.name.clone(),
                a_feature: Some(owner.clone()),
                b_feature: Some(f.id.clone()),
                a_datum: true,
            });
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
    if !marks.iter().any(|m| {
        m.claims
            .iter()
            .any(|c| c.feature.starts_with("station_witness:"))
    }) {
        return build_edges(a, marks, true);
    }
    let mut witnessed = marks.to_vec();
    for mark in &mut witnessed {
        if mark.claims.iter().any(|c| c.feature == "station_ink") {
            continue;
        }
        let span = mark.claims.iter().find_map(|c| {
            let word = c.feature.strip_prefix("station_witness:")?;
            type Witness = ([f64; 3], [f64; 3], [f64; 2], f64, Vec<[[f64; 2]; 2]>);
            let (direction, up, at, scale, lines): Witness = serde_json::from_str(word).ok()?;
            ink_span(
                a,
                &crate::output::DrawnFrame {
                    direction,
                    up,
                    at,
                    scale,
                    half_axis: None,
                },
                &lines,
            )
        });
        if mark
            .claims
            .iter()
            .any(|c| c.feature.starts_with("station_witness:"))
        {
            mark.claims.push(crate::output::Claim {
                feature: "station_ink".into(),
                parameter: "witnesses".into(),
            });
            if let Some((k, p, q)) = span {
                mark.claims.push(crate::output::Claim {
                    feature: span_claim(k, p, q),
                    parameter: "station.distance".into(),
                });
            }
        }
    }
    build_edges(a, &witnessed, true)
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
                // Table cells have no linear witnesses. Their coordinate claims refer to
                // the analytic feature station, not the trimmed-face fingerprint's centre.
                // Keep this correction in drawing evidence, outside planner precedence.
                if authored
                    && let Some(k) = c
                        .parameter
                        .strip_prefix("location.length.")
                        .and_then(|axis| AXES.iter().position(|v| *v == axis))
                    && a.features
                        .iter()
                        .any(|f| f.id == c.feature && matches!(f.kind.as_str(), "hole" | "boss"))
                {
                    if let Some(at) = station(a, &c.feature, k) {
                        for end in a.envelope.iter().take(1) {
                            // Unwitnessed coordinates use only the documented low-corner
                            // convention. High-end distances require their own ink span.
                            if states_mm(
                                mark.text.split_whitespace().next().unwrap_or(""),
                                (at - end[k]).abs(),
                            ) {
                                spans.push((k, end[k], at));
                            }
                        }
                    }
                    continue;
                }
                if authored
                    && c.parameter == "location_slot_across.length"
                    && let Some((k, at)) = blind_axis(a, &c.feature)
                    && states_mm(
                        mark.text.split_whitespace().next().unwrap_or(""),
                        (at - a.envelope[0][k]).abs(),
                    )
                {
                    spans.push((k, a.envelope[0][k], at));
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
                if authored && let Some(span) = blind_size(a, &c.feature, &c.parameter, &mark.text)
                {
                    spans.push(span);
                }
                // A compound depth has its own printed depth term and two physical
                // supports. A width or length with the same number is not depth evidence.
                if authored
                    && [
                        "pocket_depth.length",
                        "rectangular_blind_slot_depth.length",
                        "bore.depth",
                        "circular_step_depth.length",
                    ]
                    .contains(&c.parameter.as_str())
                    && let Some(s) = super::redundancy::span(a, &c.feature, &c.parameter)
                    && printed_depth(&mark.text, s.hi - s.lo)
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
                    a_datum: false,
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
    // Bind pocket walls and midplanes before deriving any half interval. Physical
    // faces share a node only by face identity, never by equal coordinates.
    let pocket_ids: BTreeSet<_> = super::pockets::placed(a)
        .into_iter()
        .map(|(f, _)| f.id.as_str())
        .collect();
    for e in &mut typed {
        let Some(mark) = marks.iter().find(|m| m.name == e.owner) else {
            continue;
        };
        for (at, node) in [(&e.a, &mut e.a_feature), (&e.b, &mut e.b_feature)] {
            if node.is_some() {
                continue;
            }
            for claim in &mark.claims {
                if claim.parameter == "location_slot_across.length"
                    && blind_axis(a, &claim.feature).is_some_and(|(k, p)| {
                        k == e.axis && (p - at).abs() < super::redundancy::TOL
                    })
                {
                    *node = Some(format!("midplane:{}", claim.feature));
                    continue;
                }

                let Some(f) = a
                    .features
                    .iter()
                    .find(|f| f.id == claim.feature && pocket_ids.contains(f.id.as_str()))
                else {
                    continue;
                };
                if claim.parameter.starts_with("location_pocket.")
                    && super::redundancy::span(a, &f.id, &claim.parameter).is_some_and(|s| {
                        s.axis == e.axis && (s.hi - at).abs() < super::redundancy::TOL
                    })
                {
                    *node = drawn_node(a, &f.id, e.axis);
                } else if ["pocket_width.length", "pocket_length.length"]
                    .contains(&claim.parameter.as_str())
                {
                    *node = pocket_face(a, &f.id, e.axis, *at);
                }
            }
        }
    }
    for e in &mut typed {
        let Some(mark) = marks.iter().find(|m| m.name == e.owner) else {
            continue;
        };
        for (at, node) in [(e.a, &mut e.a_feature), (e.b, &mut e.b_feature)] {
            if node.is_some() {
                continue;
            }
            let faces: BTreeSet<_> = mark
                .claims
                .iter()
                .flat_map(|c| {
                    let owned: Vec<_> = if c.feature == "step_level" {
                        (0..a.face_attachments.len()).collect()
                    } else {
                        a.features
                            .iter()
                            .find(|f| f.id == c.feature)
                            .map(|f| f.faces.clone())
                            .unwrap_or_default()
                    };
                    owned
                        .into_iter()
                        .filter(|&i| {
                            a.face_attachments.get(i).is_some_and(|face| {
                                face.surface.as_deref() == Some("plane")
                                    && face.axis == Some(e.axis)
                                    && face.bounds.is_some_and(|b| {
                                        (b[e.axis] - at).abs() < super::redundancy::TOL
                                            && (b[e.axis + 3] - at).abs() < super::redundancy::TOL
                                    })
                            })
                        })
                        .collect::<Vec<_>>()
                })
                .collect();
            if faces.len() == 1 {
                *node = Some(format!("face:{}", faces.first().unwrap()));
            }
        }
    }
    typed.extend(round_axis_edges(a, marks));
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
                        && e.a_feature == pocket_face(a, &f.id, s.axis, e.a)
                        && e.b_feature == pocket_face(a, &f.id, s.axis, e.b)
                        && e.a_feature.is_some()
                        && e.b_feature.is_some()
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
                        a_feature: if (end - e.a).abs() < EPS {
                            e.a_feature.clone()
                        } else {
                            e.b_feature.clone()
                        },
                        b_feature: Some(format!("midplane:{}", f.id)),
                        ..e.clone()
                    });
                }
            }
        }
    }
    typed
}

/// An authored plane reference names an actual trimmed face, not its infinite plane.
fn reference_node(a: &Analysis, key: &str, axis: usize) -> Option<String> {
    if let Some(id) = key.strip_prefix("axis:") {
        return node_feature(a, id, axis);
    }
    if let Some(i) = a.dimension_reference_face(key, axis) {
        return Some(format!("face:{i}"));
    }
    (["edge:", "vertex:"]
        .iter()
        .any(|prefix| key.starts_with(prefix))
        && a.dimension_reference(key).is_ok())
    .then(|| key.into())
}

fn physical_node(node: Option<&str>) -> bool {
    node.is_some_and(|id| {
        ["face:", "edge:", "vertex:"]
            .iter()
            .any(|prefix| id.starts_with(prefix))
    })
}

fn pocket_face(a: &Analysis, id: &str, axis: usize, at: f64) -> Option<String> {
    let f = a.features.iter().find(|f| f.id == id)?;
    let mut faces = f.faces.iter().filter(|&&i| {
        a.face_attachments.get(i).is_some_and(|face| {
            face.surface.as_deref() == Some("plane")
                && face.axis == Some(axis)
                && face.bounds.is_some_and(|b| {
                    (b[axis] - at).abs() < super::redundancy::TOL
                        && (b[axis + 3] - at).abs() < super::redundancy::TOL
                })
        })
    });
    let i = faces.next()?;
    faces.next().is_none().then(|| format!("face:{i}"))
}

fn blind_size(a: &Analysis, id: &str, role: &str, text: &str) -> Option<(usize, f64, f64)> {
    use super::section_recesses::{Grammar, grammar};
    let f = a
        .features
        .iter()
        .find(|f| f.id == id && f.kind == "section_recess")?;
    let g = grammar(f).ok()?;
    let (s, round) = match &g {
        Grammar::RectangularBlindSlot(s) => (s, false),
        Grammar::RoundBottomBlindSlot(s) => (s, true),
        _ => return None,
    };
    let (axis, size, term) = match role {
        "rectangular_blind_slot_width.length" if !round => (s.width_axis, s.width, 0),
        "rectangular_blind_slot_length.length" if !round => (s.axis, s.length, 1),
        "round_bottom_blind_slot_length.length" if round => (s.axis, s.length, 2),
        "round_bottom_blind_slot_radius.radius" if round => (s.depth_axis, s.depth, 1),
        _ => return None,
    };
    let body = text.strip_prefix(if round {
        "ROUND-BOTTOM OPEN SLOT "
    } else {
        "OPEN SLOT "
    })?;
    let word = body
        .split('×')
        .nth(term)?
        .trim()
        .split('±')
        .next()?
        .split_whitespace()
        .next()?
        .trim_start_matches('R');
    if !word.parse::<f64>().is_ok_and(|v| v.is_finite() && v >= 0.) || !states_mm(word, size) {
        return None;
    }
    let frame = crate::bridge::holes::frame(a);
    let mut unit = [0.; 3];
    unit[axis] = 1.;
    let k = crate::bridge::holes::principal(crate::bridge::holes::dir_to_part(&frame, unit))?;
    let mut low = s.origin;
    let mut high = s.origin;
    low[axis] -= size / 2.;
    high[axis] += size / 2.;
    Some((
        k,
        crate::bridge::holes::to_part(&frame, low)[k],
        crate::bridge::holes::to_part(&frame, high)[k],
    ))
}

fn blind_axis(a: &Analysis, id: &str) -> Option<(usize, f64)> {
    use super::section_recesses::{Grammar, grammar};
    let f = a
        .features
        .iter()
        .find(|f| f.id == id && f.kind == "section_recess")?;
    let (Grammar::RectangularBlindSlot(s) | Grammar::RoundBottomBlindSlot(s)) = grammar(f).ok()?
    else {
        return None;
    };
    let frame = crate::bridge::holes::frame(a);
    let mut unit = [0.; 3];
    unit[s.width_axis] = 1.;
    let k = crate::bridge::holes::principal(crate::bridge::holes::dir_to_part(&frame, unit))?;
    Some((k, crate::bridge::holes::to_part(&frame, s.origin)[k]))
}

fn drawn_node(a: &Analysis, id: &str, axis: usize) -> Option<String> {
    if id.starts_with("face:") {
        return Some(id.into());
    }
    if let Some((_, Ok(p))) = super::pockets::placed(a)
        .into_iter()
        .find(|(f, _)| f.id == id)
    {
        if axis == p.long_axis && (p.lo - a.envelope[0][axis]).abs() < EPS {
            return pocket_face(a, id, axis, p.lo);
        }
        return Some(format!("midplane:{id}"));
    }
    if blind_axis(a, id).is_some_and(|(k, _)| k == axis) {
        return Some(format!("midplane:{id}"));
    }
    node_feature(a, id, axis)
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
        mark.claims.retain(|c| {
            !c.feature.starts_with("station_span:")
                && !c.feature.starts_with("station_witness:")
                && c.feature != "station_ink"
        });
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

/// Round-axis datums terminate at their physical owner, never at every feature sharing one
/// coordinate. The datum must be witnessed by a printed edge from the unique exterior axis.
fn round_route(edges: &[Edge], axis: usize, at: f64, node: Option<&str>) -> Option<Vec<usize>> {
    let origins: BTreeSet<_> = edges
        .iter()
        .filter(|e| e.axis == axis && e.a_datum)
        .filter_map(|e| e.a_feature.as_deref())
        .collect();
    if origins.is_empty() {
        return None;
    }
    walk(edges, axis, at, node, &BTreeSet::new(), |_, id| {
        id.is_some_and(|id| origins.contains(id))
    })
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
    if let Some(path) =
        proof(a, edges, roots, axis, at, node).or_else(|| round_route(edges, axis, at, node))
    {
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
            let Some(other_at) = station(a, &other.id, axis) else {
                continue;
            };
            if let Some(path) = route(
                edges,
                roots,
                axis,
                other_at,
                Some(&other.id),
                &BTreeSet::new(),
            )
            .or_else(|| {
                round_route(
                    edges,
                    axis,
                    station(a, &other.id, axis).unwrap_or(other.fingerprint.position[axis]),
                    Some(&other.id),
                )
            }) {
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
                let node = |side: usize| refs.get(side).and_then(|r| reference_node(a, r, k));
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
            if !authored {
                e.b_feature = Some(members[0].clone());
            } else if let Some(s) = super::redundancy::span(a, &members[0], "location_slot.length")
            {
                if (e.a - s.hi).abs() < super::redundancy::TOL {
                    e.a_feature = Some(members[0].clone());
                }
                if (e.b - s.hi).abs() < super::redundancy::TOL {
                    e.b_feature = Some(members[0].clone());
                }
            }
            out.push(e);
        } else if location && (!right.is_empty() || (authored && !left.is_empty())) {
            for r in right {
                let mut e = span.clone();
                e.b_feature = Some(r.clone());
                out.push(e);
            }
            if authored {
                for l in left {
                    let mut e = span.clone();
                    e.a_feature = Some(l.clone());
                    out.push(e);
                }
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
    for f in a.features.iter().filter(|f| f.kind == "section_recess") {
        if let Some((k, at)) = blind_axis(a, &f.id) {
            out.push((f.id.clone(), k, at));
        }
    }
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
            drawn_node(a, &feature, k).as_deref(),
        );
        let (code, involved) = match first {
            None => {
                let node = drawn_node(a, &feature, k);
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
                    drawn_node(a, &feature, k).as_deref(),
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

/// Native ladder stations belong to the ladder, independently of feature centres.
/// Several trimmed supports may be one native level; this never aliases a pocket midplane.
pub(crate) fn native_stations(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    if a.features.iter().any(|f| f.kind == "turned_step") {
        return Vec::new();
    }
    let edges = drawn_edges(a, marks);
    let roots = drawing_roots(a, marks);
    let mut wanted: Vec<_> = a.levels.iter().map(|&z| (2, z)).collect();
    for r in &a.risers {
        for (axis, positions) in [(&r.axis, &r.positions), (&r.other_axis, &r.other_positions)] {
            if let Some(k) = AXES.iter().position(|v| *v == axis) {
                wanted.extend(positions.iter().map(|&p| (k, p)));
            }
        }
    }
    wanted.sort_by(|(k, p), (l, q)| k.cmp(l).then(p.total_cmp(q)));
    wanted.dedup_by(|(k, p), (l, q)| k == l && (*p - *q).abs() < super::redundancy::TOL);
    let mut issues = Vec::new();
    for (k, at) in wanted {
        if roots[k]
            .iter()
            .any(|r| (r - at).abs() < super::redundancy::TOL)
        {
            continue;
        }
        let faces: Vec<_> = a
            .face_attachments
            .iter()
            .enumerate()
            .filter(|(_, f)| {
                f.surface.as_deref() == Some("plane")
                    && f.axis == Some(k)
                    && f.bounds
                        .is_some_and(|b| (b[k] - at).abs() < super::redundancy::TOL)
            })
            .map(|(i, _)| format!("face:{i}"))
            .collect();
        let physical = faces
            .iter()
            .any(|face| drawn_proof(a, &edges, &roots[k], k, at, Some(face)).is_some());
        // A native ladder annotation names this level/shoulder explicitly. Its own
        // measured ink must agree; equal values in another feature's mark do not qualify.
        let direct = marks.iter().any(|m| {
            m.claims.iter().any(|c| {
                c.feature == "step_level"
                    && super::redundancy::span(a, &c.feature, &c.parameter).is_some_and(|s| {
                        s.axis == k
                            && (s.hi - at).abs() < super::redundancy::TOL
                            && edges.iter().any(|e| {
                                e.owner == m.name
                                    && e.axis == k
                                    && (e.a.min(e.b) - s.lo).abs() < super::redundancy::TOL
                                    && (e.a.max(e.b) - s.hi).abs() < super::redundancy::TOL
                            })
                    })
            })
        });
        if !physical && !direct {
            let parameter = if k == 2 {
                format!(
                    "step_height.length.h{}",
                    super::parameters::fmt(at - a.envelope[0][k]).replace('.', "_")
                )
            } else {
                format!(
                    "step_position.length.{}{}",
                    AXES[k],
                    super::parameters::fmt(at - a.envelope[0][k]).replace('.', "_")
                )
            };
            issues.push(uncovered(
                a,
                "feature_not_located",
                "step_level",
                &parameter,
                format!(
                    "Native ladder station {}={at}: no printed dimension chain reaches an origin.",
                    AXES[k]
                ),
            ));
        }
    }
    issues
}

/// Printed owners of a located feature's one-axis proof, for independent mutation audits.
/// An empty chain means the feature lies on a physical origin, not an inferred midpoint.
pub fn printed_chain(
    a: &Analysis,
    marks: &[Mark],
    feature: &str,
    parameter: &str,
) -> Option<Vec<String>> {
    let axis = parameter
        .rsplit('.')
        .next()
        .and_then(|s| AXES.iter().position(|axis| *axis == s))
        .or_else(|| super::redundancy::span(a, feature, parameter).map(|s| s.axis))?;
    let at = targets(a)
        .into_iter()
        .find(|(id, k, _)| id == feature && *k == axis)
        .map(|(_, _, at)| at)
        .or_else(|| pattern(a, feature).and_then(|_| station(a, feature, axis)))?;
    let edges = drawn_edges(a, marks);
    let path = drawn_proof(
        a,
        &edges,
        &drawing_roots(a, marks)[axis],
        axis,
        at,
        drawn_node(a, feature, axis).as_deref(),
    )?;
    let mut owners: Vec<_> = path.into_iter().map(|i| edges[i].owner.clone()).collect();
    owners.dedup();
    Some(owners)
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
            drawn_node(a, &feature, k).as_deref(),
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
                .filter(|(_, e)| {
                    !physical_node(e.a_feature.as_deref()) || !physical_node(e.b_feature.as_deref())
                })
                .map(|(i, _)| i)
                .collect();
            let (Some(lo), Some(hi)) = (
                pocket_face(a, &f.id, s.axis, s.lo),
                pocket_face(a, &f.id, s.axis, s.hi),
            ) else {
                continue;
            };
            if walk(&edges, s.axis, s.hi, Some(&hi), &excluded, |_, node| {
                node == Some(lo.as_str())
            })
            .is_some()
            {
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
            a_datum: false,
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
    #[test]
    fn a_derived_midplane_is_neither_another_feature_nor_a_physical_datum() {
        let mut start = edge(0., 40., "start");
        start.b_feature = Some("face:1".into());
        let mut half = edge(40., 60., "length");
        half.a_feature = Some("face:1".into());
        half.b_feature = Some("midplane:p1".into());
        let edges = [start, half];
        assert!(route(&edges, &[0.], 0, 60., Some("midplane:p1"), &BTreeSet::new()).is_some());
        assert!(route(&edges, &[0.], 0, 60., Some("midplane:p2"), &BTreeSet::new()).is_none());
        assert!(route(&edges, &[0.], 0, 60., Some("face:60"), &BTreeSet::new()).is_none());
        assert!(route(&[], &[60.], 0, 60., Some("midplane:p1"), &BTreeSet::new()).is_none());
    }

    #[test]
    fn a_compound_depth_needs_its_own_nominal_not_a_tolerance_or_other_size() {
        assert!(printed_depth("6 × 40 × 3 DEEP", 3.));
        assert!(printed_depth("6 × 40 × 3 ±0.1 DEEP", 3.));
        assert!(!printed_depth("3 × 40", 3.));
        assert!(!printed_depth("6 × 40 × 99 ±3 DEEP", 3.));
    }

    #[test]
    fn a_native_ladder_mark_must_witness_its_own_root_and_level() {
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema":"draftwright.analysis/2", "binding":{"source_sha256":"", "part":"test", "face_count":0, "loader":"test"},
            "envelope":[[0.,0.,0.],[20.,20.,10.]], "levels":[4.], "features":[], "iso_bounds":[0.,0.,0.,0.]
        })).unwrap();
        let mut mark = Mark {
            name: "d:step".into(),
            view: None,
            text: "4".into(),
            claims: vec![
                crate::output::Claim {
                    feature: "step_level".into(),
                    parameter: "step_height.length.h4".into(),
                },
                crate::output::Claim {
                    feature: span_claim(2, 0., 4.),
                    parameter: "station.distance".into(),
                },
            ],
        };
        assert!(native_stations(&a, std::slice::from_ref(&mark)).is_empty());
        mark.claims[1].feature = span_claim(2, 4., 8.);
        assert!(!native_stations(&a, &[mark]).is_empty());
        assert!(!native_stations(&a, &[]).is_empty());
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
        let mut with_bad_candidate = a.clone();
        with_bad_candidate.features.insert(
            0,
            serde_json::from_value(feature("bad", "hole", [40., 22., 5.])).unwrap(),
        );
        with_bad_candidate.recognition = Some(serde_json::from_value(serde_json::json!({
            "recogniser":"test", "frame":[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]],
            "patterns":[{"id":"bad","arrangement":"linear","members":[],"faces":[],"parameters":{}}]
        })).unwrap());
        assert!(
            drawn_proof(
                &with_bad_candidate,
                &[e.clone()],
                &[0.],
                0,
                40.,
                Some("boss")
            )
            .is_some(),
            "an unreadable candidate must not suppress a later coaxial proof"
        );
        assert!(drawn_proof(&a, &[e], &[0.], 0, 40., Some("unrelated")).is_none());
        assert!(drawn_proof(&a, &[], &[0.], 0, 40., Some("boss")).is_none());
        assert!(drawn_proof(&a, &[], &[0.], 1, 22., Some("boss")).is_none());
        let mut high = Mark {
            name: "d:hole/location.length.x".into(),
            view: Some("v:plan".into()),
            text: "60".into(),
            claims: vec![crate::output::Claim {
                feature: "hole".into(),
                parameter: "location.length.x".into(),
            }],
        };
        assert!(
            drawn_edges(&a, std::slice::from_ref(&high)).is_empty(),
            "a matching number is not a high-end witness"
        );
        high.claims.push(crate::output::Claim {
            feature: span_claim(0, 100., 40.),
            parameter: "station.distance".into(),
        });
        assert!(
            drawn_proof(
                &a,
                &drawn_edges(&a, std::slice::from_ref(&high)),
                &[0., 100.],
                0,
                40.,
                Some("boss")
            )
            .is_some()
        );
        high.claims.last_mut().unwrap().feature = span_claim(0, 40., 100.);
        assert!(
            drawn_proof(
                &a,
                &drawn_edges(&a, std::slice::from_ref(&high)),
                &[0., 100.],
                0,
                40.,
                Some("boss")
            )
            .is_some(),
            "the high-end witness may be drawn second"
        );
        high.claims.last_mut().unwrap().feature = span_claim(0, 0., 60.);
        assert!(
            drawn_proof(
                &a,
                &drawn_edges(&a, std::slice::from_ref(&high)),
                &[0., 100.],
                0,
                40.,
                Some("boss")
            )
            .is_none(),
            "the same number on unrelated witnesses grants no high-end credit"
        );
        high.claims.pop();
        high.name = "table-coordinate".into();
        assert!(drawn_edges(&a, std::slice::from_ref(&high)).is_empty());
        high.name = "d:hole/location.length.x".into();
        high.text = "999 ±60".into();
        assert!(drawn_edges(&a, &[high]).is_empty());
    }

    #[test]
    fn a_round_datum_needs_a_printed_distance_with_matching_witnesses() {
        let f = |id: &str, kind: &str, at: [f64; 3]| {
            serde_json::json!({
                "id": id, "kind": kind, "faces": [],
                "parameters": {"axis": [0., 0., 1.], "location": at,
                    "diameter": 4., "height": 10., "depth": 10., "bottom": "through"},
                "fingerprint": {"version": "test", "family": "test", "subtype": "test",
                    "sizes": {}, "position": at, "axis": [0., 0., 1.], "neighbourhood": []}
            })
        };
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema": "draftwright.analysis/2",
            "binding": {"source_sha256": "", "part": "test", "face_count": 0, "loader": "test"},
            "envelope": [[0., 0., 0.], [80., 30., 10.]], "iso_bounds": [0., 0., 0., 0.],
            "features": [f("origin", "boss", [10., 10., 5.]), f("hole", "hole", [50., 12., 5.]), f("unrelated", "hole", [10., 25., 5.])],
            "edge_ink": {"edges": [], "planes": [], "curved": [{
                "bounds": [[0., 0., 0.], [20., 20., 10.]],
                "cylinder": {"origin": [10., 10., 0.], "axis": [0., 0., 1.], "radius": 10.}
            }]}
        }))
        .unwrap();
        let mark = Mark {
            name: "printed".into(),
            view: Some("v:plan".into()),
            text: "40".into(),
            claims: vec![crate::output::Claim {
                feature: "hole".into(),
                parameter: "location.length.x".into(),
            }],
        };
        let proven = |marks: &[Mark]| {
            drawn_proof(
                &a,
                &drawn_edges(&a, marks),
                &drawing_roots(&a, marks)[0],
                0,
                50.,
                Some("hole"),
            )
            .is_some()
        };
        assert!(
            !proven(std::slice::from_ref(&mark)),
            "a number alone does not name a circular datum"
        );
        assert!(!proven(&[]));
        assert!(
            drawn_proof(
                &a,
                &drawn_edges(&a, std::slice::from_ref(&mark)),
                &drawing_roots(&a, std::slice::from_ref(&mark))[0],
                0,
                10.,
                Some("unrelated")
            )
            .is_none()
        );
        let mut ambiguous = a.clone();
        // The box and the round axis are both 5 from this hole; its table coordinate
        // cannot establish the circular datum in addition to locating the hole from the box.
        let h = ambiguous
            .features
            .iter_mut()
            .find(|f| f.id == "hole")
            .unwrap();
        h.fingerprint.position[0] = 5.;
        h.parameters
            .insert("location".into(), serde_json::json!([5., 12., 5.]));
        let mut coordinate = mark.clone();
        coordinate.text = "5".into();
        assert!(
            !drawn_edges(&ambiguous, std::slice::from_ref(&coordinate))
                .iter()
                .any(|e| e.a_datum)
        );
        let mut wrong = mark.clone();
        wrong.text = "999 ±40".into();
        assert!(!proven(&[wrong]));
        let mut witnessed = mark;
        witnessed.claims.push(crate::output::Claim {
            feature: "station_ink".into(),
            parameter: "linear".into(),
        });
        witnessed.claims.push(crate::output::Claim {
            feature: span_claim(0, 10., 50.),
            parameter: "linear".into(),
        });
        assert!(proven(std::slice::from_ref(&witnessed)));
        witnessed.claims.last_mut().unwrap().feature = span_claim(0, 20., 60.);
        assert!(!proven(&[witnessed]));
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
