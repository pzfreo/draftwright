//! Prismatic coverage (Python's `lint_prismatic_coverage` in `linting/coverage.py`): two coarse
//! sheet findings kept beside the pad and pocket ledgers ([`super::pads`], [`super::pockets`]).
//!
//! - **Pads** (`_lint_prismatic_pads`, LINT-code-pad_footprint_not_defined): a raised pad is
//!   defined when its footprint's length and width, its height and its location along both
//!   footprint axes are stated: each of [`super::pads::wanted`]'s requirements claimed by an
//!   annotation whose text prints it. A pad standing along z is also located along x (or y) when
//!   one of its edges lies on the part's box there (within 0.6 mm, Python's `tol`). A pad whose
//!   record cannot be read in the part's frame is not defined. One finding counts the pads.
//! - **Pockets** (`_lint_prismatic_pockets`, LINT-code-pocket_not_located): a lone blind pocket
//!   ([`super::pockets::placed`]) that is not open at the part's edge is located along each axis
//!   across it (x and y for a pocket cut along z; its long and width axes otherwise) when its
//!   reference point is within 1.0 mm of the part's centre there (symmetric), within 0.6 mm of
//!   the part's box minimum (the datum), or its location along that axis is stated
//!   ([`super::pockets::wanted`]'s `location_pocket` requirement, claimed and printed). The
//!   reference is the pocket's centre, its long-axis end instead when that end is within 0.6 mm of
//!   the datum. One finding counts the pockets.
//!
//! Differences from Python, each for a reason:
//!
//! - Python takes its evidence from the drawing's dimension witnesses (any dimension whose two
//!   witness points span the pad's edges, or the datum and the pocket's reference) and its
//!   structured notes; the port's drawing records neither witness points nor notes, so the
//!   evidence is the structured claim only, as for the location backstop
//!   ([`super::coverage::located`]).
//! - Python judges the converted pads and pockets draftwright modelled: a pad or pocket record
//!   its conversion dropped counts as undefined (pads) or unrecognised geometry. Rust has
//!   no conversion between Analysis and drawing: every pad and pocket is judged directly,
//!   and shoulder_coverage independently requires each projected defining riser station on
//!   the drawing, retaining the lone-transition and exact channel/through-step exemptions.
//! - Python excuses a pocket cut along z that starts on the datum only through symmetry or a
//!   witness to its centre, where draftwright's planner places no centre location for such a
//!   pocket; the port applies the datum-start exemption Python's other pockets have
//!   (LINT-code-pocket_not_located's detail: a pocket starting at the datum needs no long-axis
//!   centre dimension).
//! - Python's side pads need both footprint locations even where draftwright's compiler omits
//!   one as coincident with the datum; the port follows the pad ledger, which leaves it out.
//! - A pocket held by a recognised pocket pattern is located by the pattern in Python; the
//!   bridge drops quiddity's recess patterns, so every pocket is judged as a lone one (as in
//!   [`super::pockets`]).
//!
//! The severity is a warning: Python's is info on a part of several solids.

use super::parameters::Wanted;
use super::{LintIssue, Mark, Requirement, Severity, holes, pads, pockets};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// How far a pad's edge, or a pocket's reference, may be from the datum (Python's `tol`).
pub const TOL: f64 = 0.6;
/// How far from the part's centre a pocket's reference may be and still be symmetric about it.
pub const SYMMETRIC: f64 = 1.0;

/// Whether *w* is stated: claimed by a mark whose text prints each of its values.
fn stated(w: &Wanted, marks: &[Mark]) -> bool {
    let Ok(values) = &w.values else {
        return false;
    };
    marks.iter().any(|m| {
        m.claims
            .iter()
            .any(|c| c.feature == w.feature.id && c.parameter == w.parameter)
            && values.iter().all(|&v| holes::states(&m.text, v))
    })
}

fn issue(
    code: &str,
    message: String,
    subjects: Vec<String>,
    requirements: Vec<Requirement>,
    fix: &str,
) -> LintIssue {
    LintIssue {
        sheet: None,
        id: format!("l:{code}:prismatic"),
        code: code.into(),
        severity: Severity::Warning,
        message,
        at: None,
        view: None,
        subjects,
        requirements,
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence: None,
        fix: Some(fix.into()),
    }
}

/// The pads not defined, each with the requirements not stated (none for a pad whose record
/// cannot be read).
pub fn undefined_pads<'a>(analysis: &'a Analysis, marks: &[Mark]) -> Vec<(&'a str, Vec<String>)> {
    let wanted = pads::wanted(analysis);
    let [lo, hi] = analysis.envelope;
    let mut out = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "pad") {
        let Ok(p) = pads::placed(analysis, f) else {
            out.push((f.id.as_str(), Vec::new()));
            continue;
        };
        let missing: Vec<String> = wanted
            .iter()
            .filter(|w| w.feature.id == f.id && !stated(w, marks))
            .filter(|w| {
                // A pad standing along z is located across by an edge on the part's box.
                let along = |k: usize| {
                    w.parameter == format!("location_pad.location.{}", AXES[k])
                        && ((p.bounds[k][0] - lo[k]).abs() <= TOL
                            || (p.bounds[k][1] - hi[k]).abs() <= TOL)
                };
                !(p.axis == 2 && (along(0) || along(1)))
            })
            .map(|w| w.parameter.clone())
            .collect();
        if !missing.is_empty() {
            out.push((f.id.as_str(), missing));
        }
    }
    out
}

/// The pockets not located, each with its location requirements (by the pocket ledger's names)
/// along the axes not located.
pub fn unlocated_pockets<'a>(
    analysis: &'a Analysis,
    marks: &[Mark],
) -> Vec<(&'a str, Vec<String>)> {
    let wanted = pockets::wanted(analysis);
    let [lo, hi] = analysis.envelope;
    let mut out = Vec::new();
    for (f, p) in pockets::placed(analysis) {
        if super::pocket_patterns::member(analysis, &f.id) {
            // Pattern location and every pitch together locate its members. Reopen their
            // location warning when any physical pattern requirement loses its outcome.
            let pattern = analysis.features.iter().find(|g| {
                g.kind == "pocket_pattern"
                    && g.parameters["members"]
                        .as_array()
                        .is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(&f.id)))
            });
            if let Some(pattern) = pattern {
                let missing = super::pocket_patterns::coverage(analysis, marks)
                    .iter()
                    .filter(|i| i.subjects.contains(&pattern.id))
                    .map(|i| i.requirements[0].parameter.clone())
                    .collect::<Vec<_>>();
                if !missing.is_empty() {
                    out.push((f.id.as_str(), missing));
                }
            }
            continue;
        }
        let Ok(p) = p else {
            // Reported by the pocket ledger as unverifiable; nothing to locate here.
            continue;
        };
        if p.edge_anchored {
            continue;
        }
        let mut reference = p.centre;
        reference[p.width_axis] = p.w_center;
        if (p.lo - lo[p.long_axis]).abs() <= TOL {
            reference[p.long_axis] = p.lo;
        }
        let across: Vec<usize> = if p.depth_axis == 2 {
            vec![0, 1]
        } else {
            vec![p.long_axis, p.width_axis]
        };
        let missing: Vec<String> = across
            .into_iter()
            .filter_map(|k| {
                let symmetric = (reference[k] - (lo[k] + hi[k]) / 2.0).abs() <= SYMMETRIC;
                let on_datum = (reference[k] - lo[k]).abs() <= TOL;
                let parameter = if p.depth_axis == 2 {
                    format!("location_pocket.location.{}", AXES[k])
                } else {
                    format!("location_pocket.{}", AXES[k])
                };
                let claimed = wanted
                    .iter()
                    .any(|w| w.feature.id == f.id && w.parameter == parameter && stated(w, marks));
                (!(symmetric || on_datum || claimed)).then_some(parameter)
            })
            .collect();
        if !missing.is_empty() {
            out.push((f.id.as_str(), missing));
        }
    }
    out
}

/// The prismatic findings: pads not defined, then pockets not located.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    issues.extend(shoulder_coverage(analysis, marks));
    let pads = undefined_pads(analysis, marks);
    if !pads.is_empty() {
        issues.push(issue(
            "pad_footprint_not_defined",
            format!(
                "{} rectangular raised pad(s) lack footprint size, attachment-axis height, or \
                 in-plane location dimensions",
                pads.len()
            ),
            pads.iter().map(|(id, _)| id.to_string()).collect(),
            pads.iter()
                .flat_map(|(id, ps)| {
                    ps.iter().map(|p| Requirement {
                        feature: id.to_string(),
                        parameter: p.clone(),
                    })
                })
                .collect(),
            "State each pad's length and width in the view that looks down on it, its height \
             side-on, and its location from the part's corner along both footprint axes.",
        ));
    }
    let pockets = unlocated_pockets(analysis, marks);
    if !pockets.is_empty() {
        issues.push(issue(
            "pocket_not_located",
            format!(
                "{} blind pocket(s) have no complete X/Y location scheme",
                pockets.len()
            ),
            pockets.iter().map(|(id, _)| id.to_string()).collect(),
            pockets
                .iter()
                .flat_map(|(id, ps)| {
                    ps.iter().map(|p| Requirement {
                        feature: id.to_string(),
                        parameter: p.clone(),
                    })
                })
                .collect(),
            "Locate each pocket from the part's corner along each axis named, in the view that \
             looks into it.",
        ));
    }
    issues
}

/// Project the recognised risers independently of dimension selection. Two or more shoulder
/// stations describe defining geometry; a lone transition can belong to plate thickness.
fn shoulder_coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut shoulders = Vec::new();
    for r in &analysis.risers {
        let on = |z: f64| {
            r.body_levels.iter().any(|level| {
                (z - level).abs() < r.tol
                    && analysis.levels.iter().any(|l| (l - level).abs() < r.tol)
            })
        };
        let tied = if r.vertical {
            on(r.z_lo)
        } else {
            (r.lo_at_envelope || on(r.z_lo)) && (r.hi_at_envelope || on(r.z_hi))
        };
        if tied {
            for (axis, positions) in [(&r.axis, &r.positions), (&r.other_axis, &r.other_positions)]
            {
                if let Some(k) = AXES.iter().position(|a| a == axis) {
                    shoulders.extend(positions.iter().filter(|x| x.is_finite()).map(|&x| (k, x)));
                }
            }
        }
    }
    shoulders.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
    shoulders.dedup_by(|a, b| a.0 == b.0 && (a.1 - b.1).abs() < 0.001);
    if shoulders.len() < 2 {
        return Vec::new();
    }
    let qframe = crate::bridge::holes::frame(analysis);
    let channels = super::channels::placed(analysis);
    let mut issues = Vec::new();
    for (k, at) in shoulders {
        let distance = at - analysis.envelope[0][k];
        if distance.abs() < 1e-6 {
            continue;
        }
        let parameter = crate::geometry_ids::position_parameter(analysis, k, at);
        let claimed = marks.iter().any(|m| {
            m.claims.iter().any(|c| {
                if c.feature != "step_level" {
                    return false;
                }
                if c.parameter == parameter {
                    return holes::states(&m.text, distance);
                }
                // Older captured drawings still carry value-word claims.
                let prefix = format!("step_position.length.{}", AXES[k]);
                let Some(word) = c.parameter.strip_prefix(&prefix) else {
                    return false;
                };
                let encoded = word.replace('m', "-").replace('_', ".");
                encoded
                    .parse::<f64>()
                    .is_ok_and(|v| (v - distance).abs() <= 0.001)
                    && holes::states(&m.text, distance)
            })
        });
        // Exact feature geometry supplies the other Python owners; its own ledger still
        // checks their required ink, so missing channel/step dimensions are never excused.
        let channel = channels.iter().any(|(_, c)| {
            c.as_ref()
                .is_ok_and(|c| c.width_axis == k && c.walls.iter().any(|w| (w - at).abs() <= 0.001))
        });
        let bend = analysis
            .features
            .iter()
            .filter(|f| f.kind == "through_step")
            .any(|f| {
                let p = &f.parameters;
                let Some(run) = p
                    .get("axis")
                    .and_then(serde_json::Value::as_str)
                    .and_then(|v| AXES.iter().position(|a| *a == v))
                else {
                    return false;
                };
                let Some(mut point) = crate::bridge::holes::point(p.get("at")) else {
                    return false;
                };
                let Some(bend) = p
                    .get("section")
                    .and_then(serde_json::Value::as_array)
                    .and_then(|s| s.get(1))
                    .and_then(serde_json::Value::as_array)
                else {
                    return false;
                };
                let across: Vec<_> = (0..3).filter(|i| *i != run).collect();
                for (i, axis) in across.iter().enumerate() {
                    let Some(value) = bend.get(i).and_then(serde_json::Value::as_f64) else {
                        return false;
                    };
                    point[*axis] = value;
                }
                let world = crate::bridge::holes::to_part(&qframe, point);
                let mut direction = [0.0; 3];
                direction[run] = 1.0;
                let run = crate::bridge::holes::principal(crate::bridge::holes::dir_to_part(
                    &qframe, direction,
                ));
                run.is_some_and(|r| r != 2 && k != r && k != 2) && (world[k] - at).abs() <= 0.001
            });
        if claimed || channel || bend {
            continue;
        }
        issues.push(super::uncovered(analysis, "unrecognised_defining_geometry", "step_level", &parameter,
            format!("The recognised {} shoulder at {at} has no stated position on the drawing (distance {distance} from the part's box).", AXES[k])));
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::super::section_recesses::tests::{Z, recess};
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn mark(feature: &str, text: &str, parameters: &[&str]) -> Mark {
        Mark {
            name: format!("m:{text}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: parameters
                .iter()
                .map(|p| Claim {
                    feature: feature.into(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    fn codes(issues: &[LintIssue]) -> Vec<String> {
        issues
            .iter()
            .map(|i| {
                let r: Vec<&str> = i
                    .requirements
                    .iter()
                    .map(|r| r.parameter.as_str())
                    .collect();
                format!("{} {}", i.message, r.join(","))
            })
            .collect()
    }

    #[test]
    fn shoulder_face_claims_check_the_printed_value_and_survive_resizing() {
        let mut a = part("pad", json!({}));
        a.features.clear();
        a.envelope = [[0., 0., 0.], [100., 100., 10.]];
        a.levels = vec![0., 10.];
        a.risers = serde_json::from_value(json!([{
            "faces":[7], "vertical":true, "axis":"x", "positions":[20.,40.],
            "other_axis":"y", "other_positions":[], "z_lo":0., "z_hi":10.,
            "lo_at_envelope":true, "hi_at_envelope":true, "tol":0.001, "body_levels":[0.,10.]
        }]))
        .unwrap();
        let x = mark("step_level", "20", &["step_position.length.f7_0"]);
        let y = mark("step_level", "40", &["step_position.length.f7_1"]);
        assert!(shoulder_coverage(&a, &[x.clone(), y]).is_empty());
        let missing = shoulder_coverage(&a, std::slice::from_ref(&x));
        assert_eq!(missing.len(), 1);
        assert_eq!(
            missing[0].requirements[0].parameter,
            "step_position.length.f7_1"
        );
        assert_eq!(
            shoulder_coverage(
                &a,
                &[x, mark("step_level", "41", &["step_position.length.f7_1"])]
            )
            .len(),
            1
        );
        assert!(
            shoulder_coverage(
                &a,
                &[
                    mark("step_level", "20", &["step_position.length.x20"]),
                    mark("step_level", "40", &["step_position.length.x40"]),
                ]
            )
            .is_empty()
        );
        a.risers[0].positions = vec![25., 45.];
        assert!(
            shoulder_coverage(
                &a,
                &[
                    mark("step_level", "25", &["step_position.length.f7_0"]),
                    mark("step_level", "45", &["step_position.length.f7_1"]),
                ]
            )
            .is_empty()
        );
    }

    /// test_issue_885's case study, one pad: 40 × 18 × 14 high standing on a 22 mm plate at x
    /// 15..55 and y 0..18 of a part whose box is 100 × 100 here. Its y edge lies on the box, so
    /// only its x location is wanted beside its sizes and height; taking one size off is reported
    /// (test_removing_one_pad_size_is_not_credited_from_an_aligned_sibling), and nothing stated
    /// reports the pad (test_omitting_furniture_reports_both_coverage_gaps_and_reduces_score).
    #[test]
    fn a_pad_is_defined_by_its_sizes_height_and_location() {
        let mut a = part(
            "pad",
            json!({ "x0": 15.0, "x1": 55.0, "y0": 0.0, "y1": 18.0, "z0": 22.0, "z1": 36.0,
                    "axis": "z", "direction": 1 }),
        );
        a.envelope = [[0.0, 0.0, 0.0], [100.0, 100.0, 36.0]];
        let id = "f:pad:1";
        let sizes = mark(
            id,
            "40 × 18 × 14 HIGH",
            &["pad_width.length", "pad_length.length", "pad_height.length"],
        );
        let x = mark(id, "35", &["location_pad.location.x"]);
        assert!(coverage(&a, &[sizes.clone(), x.clone()]).is_empty());
        let no_width = mark(
            id,
            "40 × 14 HIGH",
            &["pad_length.length", "pad_height.length"],
        );
        assert_eq!(
            codes(&coverage(&a, &[no_width, x])),
            [
                "1 rectangular raised pad(s) lack footprint size, attachment-axis height, or \
              in-plane location dimensions pad_width.length"
            ]
        );
        assert_eq!(
            codes(&coverage(&a, &[])),
            [
                "1 rectangular raised pad(s) lack footprint size, attachment-axis height, or \
              in-plane location dimensions pad_width.length,pad_length.length,\
              pad_height.length,location_pad.location.x"
            ]
        );
        // Moved off the box's edge, it must be located along y too.
        a.features[0].parameters["y0"] = json!(10.0);
        a.features[0].parameters["y1"] = json!(28.0);
        assert_eq!(
            undefined_pads(
                &a,
                &[sizes.clone(), mark(id, "35", &["location_pad.location.x"])]
            ),
            [(id, vec!["location_pad.location.y".to_string()])]
        );
        assert!(
            undefined_pads(
                &a,
                &[
                    sizes,
                    mark(id, "35", &["location_pad.location.x"]),
                    mark(id, "19", &["location_pad.location.y"])
                ]
            )
            .is_empty()
        );
    }

    /// A pocket cut along z, 42 × 18 at (56, 30) on a 100 × 100 box (the case study's obround
    /// slots, as rectangles): located by its x and y locations; one left off is reported; centred
    /// across (within 1.0 mm of the box's centre) it needs none that way; starting on the datum
    /// along its length it needs none along it; open at the part's edge it is not located at all.
    #[test]
    fn a_pocket_is_located_by_symmetry_the_datum_or_a_stated_location() {
        let rect = [
            ([-21.0, -9.0], 0.0),
            ([21.0, -9.0], 0.0),
            ([21.0, 9.0], 0.0),
            ([-21.0, 9.0], 0.0),
        ];
        let pocket = |x: f64, y: f64| {
            part(
                "section_recess",
                recess(
                    "pocket",
                    "rectangular",
                    [[x, y, 2.0], Z[1], Z[2], Z[3]],
                    [0.0, 8.0],
                    [false, true],
                    true,
                    &rect,
                ),
            )
        };
        let id = "f:section_recess:1";
        let a = pocket(56.0, 30.0);
        let x = mark(id, "56", &["location_pocket.location.x"]);
        let y = mark(id, "30", &["location_pocket.location.y"]);
        assert!(coverage(&a, &[x.clone(), y]).is_empty());
        assert_eq!(
            codes(&coverage(&a, std::slice::from_ref(&x))),
            ["1 blind pocket(s) have no complete X/Y location scheme location_pocket.location.y"]
        );
        // A misprinted location locates nothing.
        assert_eq!(
            unlocated_pockets(
                &a,
                &[x.clone(), mark(id, "31", &["location_pocket.location.y"])]
            ),
            [(id, vec!["location_pocket.location.y".to_string()])]
        );
        // Centred across y (50.6 against the box's 50).
        assert!(unlocated_pockets(&pocket(56.0, 50.6), &[x]).is_empty());
        // Starting on the datum along x (x 0..42), centred in y: nothing to locate.
        assert!(unlocated_pockets(&pocket(21.0, 50.0), &[]).is_empty());
        // Open at the part's edge: located by the edge.
        let open = [
            ([-21.0, 9.0], 0.0),
            ([-21.0, -9.0], 0.0),
            ([21.0, -9.0], 0.0),
            ([21.0, 9.0], 0.0),
        ];
        let mut edge = recess(
            "edge_open_recess",
            "polygonal",
            [[56.0, 30.0, 2.0], Z[1], Z[2], Z[3]],
            [0.0, 8.0],
            [false, true],
            false,
            &open,
        );
        edge["geometry"]["profile"]["opening"] = json!([[21.0, 9.0], [-21.0, 9.0]]);
        assert!(unlocated_pockets(&part("section_recess", edge), &[]).is_empty());
    }
}
