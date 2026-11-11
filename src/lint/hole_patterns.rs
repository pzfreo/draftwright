//! Hole-pattern location and spacing, from physical members and printed measurement claims.
//! A proven grid is located at its centre; other arrays need one physical member anchor.

use super::{LintIssue, Mark, holes};
use crate::bridge::holes::{self as geometry, frame, point, to_part};
use crate::model::Analysis;

const TOL: f64 = 0.01;
// Model/recognition coordinates are rounded to 0.001 mm.
const CENTRE_TOL: f64 = 1e-3;

/// A shared centre is evidence only in a true-shape view that actually draws the
/// round outline, the pattern's pitch circle and their common centre mark.
pub(crate) fn concentric_on_drawing(
    a: &Analysis,
    drawing: &crate::output::Drawing,
) -> std::collections::BTreeMap<String, std::collections::BTreeSet<&'static str>> {
    use crate::output::{Curve, Kind};
    let mut located = std::collections::BTreeMap::<_, std::collections::BTreeSet<_>>::new();
    let Some(ink) = &a.edge_ink else {
        return located;
    };
    for p in a.recognition.iter().flat_map(|r| &r.patterns) {
        if p.arrangement != "bolt_circle" {
            continue;
        }
        let Some(c) = point(p.parameters.get("center")).map(|c| to_part(&frame(a), c)) else {
            continue;
        };
        let Some(diameter) = p.parameters.get("diameter").and_then(|v| v.as_f64()) else {
            continue;
        };
        for (name, f) in &drawing.sheet.frames {
            let Some(axis) = (0..3).find(|&k| f.direction[k].abs() > 1. - 1e-8) else {
                continue;
            };
            let across: Vec<_> = (0..3).filter(|&k| k != axis).collect();
            // Recognition rounds model coordinates to 0.001 mm.
            // Both extrema must be tangents of this cylinder within that tolerance. A round boss
            // elsewhere, or mere coincidence with the bounding-box centre, proves nothing.
            let radius = ink
                .curved
                .iter()
                .filter_map(|s| s.cylinder.as_ref())
                .find_map(|s| {
                    (s.axis[axis].abs() > 1. - 1e-8
                        && across.iter().all(|&k| {
                            (s.origin[k] - c[k]).abs() <= CENTRE_TOL
                                && (s.origin[k] - s.radius - a.envelope[0][k]).abs() <= CENTRE_TOL
                                && (s.origin[k] + s.radius - a.envelope[1][k]).abs() <= CENTRE_TOL
                        }))
                    .then_some(s.radius * f.scale)
                });
            let Some(radius) = radius else { continue };
            let centre = f.at;
            // The printed pitch circle also uses the rounded recognition centre.
            let paper_tol = (CENTRE_TOL * f.scale.abs()).max(1e-6);
            let near = |p: [f64; 2], q: [f64; 2]| (p[0] - q[0]).hypot(p[1] - q[1]) <= paper_tol;
            let outline = drawing
                .sheet
                .views
                .iter()
                .filter(|v| v.name == format!("v:{name}"))
                .flat_map(|v| &v.edges)
                .any(|edge| {
                    matches!(edge,
                    Curve::Conic { centre: c, radii, t0, t1, .. }
                        if near(*c, centre) && radii.iter().all(|r| (*r - radius).abs() <= paper_tol)
                            && ((t1 - t0).abs() - std::f64::consts::TAU).abs() < 1e-6)
                });
            let marked = drawing
                .sheet
                .centre_marks
                .get(name)
                .is_some_and(|marks| marks.iter().any(|c| near(*c, centre)));
            let pitch = diameter * f.scale / 2.;
            let ring = drawing.sheet.items.iter().any(|item| {
                if item.kind != Kind::Centreline
                    || item.name != format!("ink:v:{name}:{}", p.id)
                    || item.lines.len() < 3
                {
                    return false;
                }
                let on_circle = item.lines.iter().flatten().all(|q| {
                    ((q[0] - centre[0]).hypot(q[1] - centre[1]) - pitch).abs() <= paper_tol
                });
                let closed = item
                    .lines
                    .iter()
                    .zip(item.lines.iter().cycle().skip(1))
                    .all(|(a, b)| (a[1][0] - b[0][0]).hypot(a[1][1] - b[0][1]) < 1e-6);
                let sweep: f64 = item
                    .lines
                    .iter()
                    .map(|[a, b]| {
                        let a = [a[0] - centre[0], a[1] - centre[1]];
                        let b = [b[0] - centre[0], b[1] - centre[1]];
                        (a[0] * b[1] - a[1] * b[0])
                            .atan2(a[0] * b[0] + a[1] * b[1])
                            .abs()
                    })
                    .sum();
                on_circle && closed && (sweep - std::f64::consts::TAU).abs() < 1e-6
            });
            if outline && marked && ring {
                for k in &across {
                    located.entry(p.id.clone()).or_default().insert(
                        [
                            "location.length.x",
                            "location.length.y",
                            "location.length.z",
                        ][*k],
                    );
                }
            }
        }
    }
    located
}

fn stations(centres: &[[f64; 3]], axis: usize) -> Vec<f64> {
    let mut values: Vec<f64> = centres.iter().map(|c| c[axis]).collect();
    values.sort_by(f64::total_cmp);
    values.dedup_by(|a, b| (*a - *b).abs() <= TOL);
    values
}

fn even(values: &[f64]) -> bool {
    values.len() < 3
        || values
            .windows(2)
            .all(|w| ((w[1] - w[0]) - (values[1] - values[0])).abs() <= TOL)
}

/// A repeated pitch prints the number of physical intervals, never the member count.
fn pitch_states(text: &str, pitch: f64, intervals: usize) -> bool {
    match text.split_once('×') {
        Some((count, nominal)) => {
            count.trim().parse::<usize>() == Ok(intervals) && holes::states(nominal, pitch)
        }
        None => intervals == 1 && holes::states(text, pitch),
    }
}

/// Exact pattern claims or truthful physical-member ordinates prove location and spacing.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let read: Vec<_> = geometry::read(analysis)
        .into_iter()
        .filter_map(|(_, h)| h.ok())
        .collect();
    let refs: Vec<_> = read.iter().collect();
    let bosses = geometry::bosses(analysis);
    let mut issues = Vec::new();
    let Some(recognition) = &analysis.recognition else {
        return issues;
    };
    for p in &recognition.patterns {
        let Ok(members) = geometry::established_with(p, &refs, &bosses) else {
            continue;
        };
        let Some((axis, _)) = read[members[0]].principal else {
            continue;
        };
        let axes: Vec<_> = (0..3).filter(|&k| k != axis).collect();
        let (u, v) = (axes[0], axes[1]);
        let centres: Vec<_> = members.iter().map(|&i| read[i].at).collect();
        let cols = stations(&centres, u);
        let rows = stations(&centres, v);
        let lattice = p.arrangement == "grid"
            && cols.len() * rows.len() == centres.len()
            && even(&cols)
            && even(&rows)
            && cols.iter().all(|x| {
                rows.iter().all(|y| {
                    centres
                        .iter()
                        .filter(|c| (c[u] - x).abs() <= TOL && (c[v] - y).abs() <= TOL)
                        .count()
                        == 1
                })
            });
        let mut anchors = centres.clone();
        let mut requirements: Vec<(String, Option<f64>)> = Vec::new();
        if p.arrangement == "bolt_circle" {
            anchors = point(p.parameters.get("center"))
                .map(|c| vec![to_part(&frame(analysis), c)])
                .unwrap_or_default();
            requirements.push((
                "bolt_circle.diameter".into(),
                p.parameters.get("diameter").and_then(|d| d.as_f64()),
            ));
        } else if lattice {
            let mut centre = centres[0];
            centre[u] = (cols[0] + cols[cols.len() - 1]) / 2.0;
            centre[v] = (rows[0] + rows[rows.len() - 1]) / 2.0;
            anchors = vec![centre];
            for (name, values) in [("col", &cols), ("row", &rows)] {
                if values.len() > 1 {
                    requirements.push((
                        format!("grid_pitch.length.{name}"),
                        Some(values[1] - values[0]),
                    ));
                }
            }
        } else if p.arrangement == "linear" {
            let pitch = if rows.len() == 1 && even(&cols) && cols.len() > 1 {
                Some(cols[1] - cols[0])
            } else if cols.len() == 1 && even(&rows) && rows.len() > 1 {
                Some(rows[1] - rows[0])
            } else {
                p.parameters.get("pitch").and_then(|x| x.as_f64())
            };
            requirements.push(("pitch.length".into(), pitch));
        } else {
            for name in ["col", "row"] {
                requirements.push((format!("grid_pitch.length.{name}"), None));
            }
        }
        let spacing_stated = |parameter: &str, text: &str, value: f64| {
            let intervals = match parameter {
                "grid_pitch.length.col" => Some(cols.len().saturating_sub(1)),
                "grid_pitch.length.row" => Some(rows.len().saturating_sub(1)),
                "pitch.length" if rows.len() == 1 => Some(cols.len().saturating_sub(1)),
                "pitch.length" if cols.len() == 1 => Some(rows.len().saturating_sub(1)),
                "pitch.length" => Some(centres.len().saturating_sub(1)),
                _ => None,
            };
            intervals.map_or_else(
                || holes::states(text, value),
                |n| pitch_states(text, value, n),
            )
        };
        let locations = |printed_only: bool| -> Vec<_> {
            members
                .iter()
                .map(|&i| {
                    [u, v].map(|k| {
                        let distance = read[i].at[k] - analysis.envelope[0][k];
                        let parameter = format!("location.length.{}", ["x", "y", "z"][k]);
                        distance.abs() <= 1e-6
                            || marks
                                .iter()
                                .filter(|m| {
                                    !printed_only || !m.name.starts_with("location_network:")
                                })
                                .any(|m| {
                                    m.claims.iter().any(|c| {
                                        c.feature == read[i].feature.id && c.parameter == parameter
                                    }) && m.states_text(&m.text, distance)
                                })
                    })
                })
                .collect()
        };
        let member_locations = locations(false);
        let printed_member_locations = locations(true);
        let all_members_located = member_locations.iter().all(|axes| axes.iter().all(|b| *b));
        let member_anchor_with_pitch = member_locations.iter().any(|axes| axes.iter().all(|b| *b))
            && requirements.iter().all(|(parameter, value)| {
                value.is_some_and(|d| {
                    marks.iter().any(|m| {
                        m.claims
                            .iter()
                            .any(|c| c.feature == p.id && c.parameter == *parameter)
                            && spacing_stated(parameter, &m.text, m.units.from_mm(d))
                    })
                })
            });
        let member_spacing = p.arrangement == "linear" || p.arrangement == "grid";
        // Stream K6 (ADR4-U1): a pitch along an axis on which every member is also located
        // states a separation the locations already fix: over-dimensioning that is neither
        // identical nor coincident. Each such pitch is reported, naming its marks.
        if member_spacing {
            for (position, k) in [u, v].into_iter().enumerate() {
                let along = if position == 0 { &cols } else { &rows };
                if along.len() < 2 || !printed_member_locations.iter().all(|axes| axes[position]) {
                    continue;
                }
                let parameter = if p.arrangement == "grid" {
                    format!("grid_pitch.length.{}", ["col", "row"][position])
                } else if (position == 0) == (rows.len() == 1) {
                    "pitch.length".to_string()
                } else {
                    continue;
                };
                let pitch = along[1] - along[0];
                let Some(mark) = marks.iter().find(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == p.id && c.parameter == parameter)
                        && m.states_text(&m.text, pitch)
                }) else {
                    continue;
                };
                let mut issue = super::uncovered(
                    analysis,
                    "over_dimensioned_pattern",
                    &p.id,
                    &parameter,
                    format!(
                        "{} states its pitch along {} and every member's {} location too; the locations fix the pitch, so one of them is redundant.",
                        p.id,
                        ["x", "y", "z"][k],
                        ["x", "y", "z"][k]
                    ),
                );
                issue.annotation = Some(mark.name.clone());
                issue.related = p.members.clone();
                issue.fix = Some(format!(
                    "Omit the repeated {} dimension, or locate the pattern once and keep its pitch.",
                    parameter.replace(['_', '.'], " ")
                ));
                issues.push(issue);
            }
        }
        for (position, k) in [u, v].into_iter().enumerate() {
            if member_spacing
                && (member_locations.iter().all(|axes| axes[position]) || member_anchor_with_pitch)
            {
                continue;
            }

            let parameter = format!("location.length.{}", ["x", "y", "z"][k]);
            let distances: Vec<_> = anchors
                .iter()
                .map(|c| c[k] - analysis.envelope[0][k])
                .collect();
            // A zero datum distance needs no numeric ordinate, as in the location backstop.
            if distances.iter().any(|d| d.abs() <= 1e-6) {
                continue;
            }
            let claiming: Vec<_> = marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == p.id && c.parameter == parameter)
                })
                .collect();
            if claiming
                .iter()
                .any(|m| distances.iter().any(|d| m.states_text(&m.text, *d)))
            {
                continue;
            }
            let mut issue = super::uncovered(
                analysis,
                "hole_pattern_requirement_missing",
                &p.id,
                &parameter,
                format!(
                    "{} has no proven {} location of its {} on the drawing.",
                    p.id,
                    ["x", "y", "z"][k],
                    if lattice || p.arrangement == "bolt_circle" {
                        "centre"
                    } else {
                        "physical member anchor"
                    }
                ),
            );
            issue.related = p.members.clone();
            issues.push(issue);
        }
        for (parameter, value) in requirements {
            // Fully located members state their separations without a redundant pitch label.
            let pitch_axis = match parameter.as_str() {
                "grid_pitch.length.col" => Some(0),
                "grid_pitch.length.row" => Some(1),
                "pitch.length" if rows.len() == 1 => Some(0),
                "pitch.length" if cols.len() == 1 => Some(1),
                _ => None,
            };
            if member_spacing
                && (all_members_located
                    || pitch_axis
                        .is_some_and(|axis| member_locations.iter().all(|axes| axes[axis])))
            {
                continue;
            }
            let claiming: Vec<_> = marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == p.id && c.parameter == parameter)
                })
                .collect();
            if value.is_some_and(|d| {
                claiming
                    .iter()
                    .any(|m| spacing_stated(&parameter, &m.text, m.units.from_mm(d)))
            }) {
                continue;
            }
            let code = if value.is_none() || !claiming.is_empty() {
                "hole_pattern_requirement_unverifiable"
            } else {
                "hole_pattern_requirement_missing"
            };
            let mut issue = super::uncovered(
                analysis,
                code,
                &p.id,
                &parameter,
                format!(
                    "{}: {parameter} is not proved by the printed drawing.",
                    p.id
                ),
            );
            issue.related = p.members.clone();
            issue.annotation = claiming.first().map(|m| m.name.clone());
            issue.evidence = value.map(|d| format!("Expected {}", super::parameters::fmt(d)));
            issues.push(issue);
        }
    }
    issues
}
