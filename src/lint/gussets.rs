//! Gusset rib coverage (Python's `linting/gusset_rib_coverage.py`): each rib states its thickness
//! and its two legs, and a group of ribs states its pitch (a linear array) or its spacing (a
//! mirrored pair), and its location from the part's box (LINT-gusset_rib_coverage-requirements,
//! LINT-code-gusset_rib_requirement_state).
//!
//! quiddity's `GussetRib` (`thickness_axis`, `thickness_bounds`, the two `supports` planes at its
//! square corner, its `legs` from them and their `directions`) reaches the Analysis as a
//! `gusset_rib` feature, and its `GussetRibArray` (`ribs`, `pitch`) and `GussetRibMirrorPair`
//! (`ribs`, `mirror_plane`) as `gusset_rib_pattern` features carrying their members' records.
//! Each pattern is one group of its member ribs, and every other rib a group of one, as
//! draftwright's conversion makes one `GussetRibFeature` of each (`_append_gusset_features`);
//! a group is read in the part's frame ([`groups`]) and checked as that feature is: one common
//! section, one thickness (within 1e-6), members ordered along the thickness axis, a linear
//! array's centres one pitch apart and a mirrored pair's about its plane (within 1e-6).
//!
//! A group has, judged by the claiming annotation's printed text, on the group (the pattern, or
//! the lone rib):
//!
//! - for each rib, `gusset_thickness.length` and its two legs `gusset_leg.length.<axis>` (one
//!   requirement per rib, as in Python, though one callout states them for the group);
//! - for an array `gusset_pitch.length`, for a mirrored pair `gusset_spacing.length`;
//! - `gusset_location.length`: from the part's box along the thickness axis to the first rib's
//!   centre, or a mirrored pair's plane.
//!
//! A group that cannot be read, or fails those checks, has no feature in draftwright: each of its
//! ribs is reported `gusset_thickness.length` unverifiable, as Python reports a rib with no IR
//! owner. Not reached: `_suppressed` and `dropped` and the assembly severity.

use serde_json::Value;

use super::parameters::fmt;
use super::{LintIssue, Mark, Requirement, Severity, holes};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// One rib in the part's frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Rib {
    pub axis: usize,
    pub bounds: [f64; 2],
    /// The support planes' axes (ascending) and coordinates, with each leg and its direction.
    pub supports: [(usize, f64); 2],
    pub legs: [f64; 2],
    pub directions: [f64; 2],
}

/// How a group's ribs are arranged.
#[derive(Clone, Debug, PartialEq)]
pub enum Arrangement {
    Single,
    Linear { pitch: f64 },
    Mirror { axis: usize, plane: f64 },
}

/// One group of ribs: the feature that owns its measurements, the rib features (or the pattern,
/// for a member with no rib feature) each row of thickness and legs is about, and the group read
/// in the part's frame, or why it cannot be.
pub struct Group<'a> {
    pub owner: &'a Feature,
    pub members: Vec<&'a Feature>,
    pub read: Result<(Vec<Rib>, Arrangement), String>,
}

/// A quiddity axis letter as the part's axis and the sign it maps with.
fn axis(frame: &[[f64; 3]; 4], v: Option<&Value>) -> Option<(usize, f64)> {
    let a = AXES
        .iter()
        .position(|x| Some(*x) == v.and_then(Value::as_str))?;
    let mut e = [0.0; 3];
    e[a] = 1.0;
    let d = dir_to_part(frame, e);
    let k = (0..3).find(|&k| (d[k].abs() - 1.0).abs() <= 1e-9)?;
    Some((k, d[k].signum()))
}

/// A rib record read in the part's frame.
fn rib(frame: &[[f64; 3]; 4], r: &Value) -> Result<Rib, String> {
    let (k, s) = axis(frame, r.get("thickness_axis")).ok_or("its thickness axis is unreadable")?;
    let pair = |v: Option<&Value>| -> Option<[f64; 2]> {
        let a = v?.as_array()?;
        Some([a.first()?.as_f64()?, a.get(1)?.as_f64()?])
    };
    let t = pair(r.get("thickness_bounds")).ok_or("its thickness bounds are unreadable")?;
    let b = [frame[0][k] + s * t[0], frame[0][k] + s * t[1]];
    let legs = pair(r.get("legs")).ok_or("its legs are unreadable")?;
    let directions = pair(r.get("directions")).ok_or("its directions are unreadable")?;
    let supports = r
        .get("supports")
        .and_then(Value::as_array)
        .filter(|s| s.len() == 2)
        .ok_or("its supports are unreadable")?;
    let mut sides = Vec::new();
    for (i, support) in supports.iter().enumerate() {
        let (j, sj) = axis(frame, support.get(0)).ok_or("a support's axis is unreadable")?;
        let at = support
            .get(1)
            .and_then(Value::as_f64)
            .ok_or("a support's plane is unreadable")?;
        sides.push(((j, frame[0][j] + sj * at), legs[i], directions[i] * sj));
    }
    sides.sort_by_key(|(s, _, _)| s.0);
    Ok(Rib {
        axis: k,
        bounds: [b[0].min(b[1]), b[0].max(b[1])],
        supports: [sides[0].0, sides[1].0],
        legs: [sides[0].1, sides[1].1],
        directions: [sides[0].2, sides[1].2],
    })
}

/// The facts quiddity identifies a rib by, as the record gives them.
fn facts(r: &Value) -> Vec<Option<&Value>> {
    [
        "thickness_axis",
        "thickness_bounds",
        "supports",
        "legs",
        "directions",
    ]
    .iter()
    .map(|k| r.get(*k))
    .collect()
}

/// Whether a pattern's member records are listed with their centres strictly ascending in
/// quiddity's own coordinates, as `GussetRibFeature` requires of the members it is given (an
/// unreadable record is left to [`rib`] to refuse).
fn in_order(records: &[&Value]) -> bool {
    let centres: Vec<f64> = records
        .iter()
        .filter_map(|r| {
            let t = r.get("thickness_bounds")?.as_array()?;
            Some((t.first()?.as_f64()? + t.get(1)?.as_f64()?) / 2.0)
        })
        .collect();
    centres.windows(2).all(|w| w[1] > w[0])
}

/// A group's ribs and arrangement, checked as draftwright's `GussetRibFeature` is. The members
/// are sorted along the part's thickness axis, which reverses quiddity's order (already checked
/// by [`in_order`]) when the part's frame flips that axis.
fn check(mut ribs: Vec<Rib>, arrangement: Arrangement) -> Result<(Vec<Rib>, Arrangement), String> {
    let first = ribs[0].clone();
    let same = |r: &Rib| {
        r.axis == first.axis
            && r.supports == first.supports
            && r.legs == first.legs
            && r.directions == first.directions
    };
    if !ribs.iter().all(same) {
        return Err("a gusset-rib pattern must have one common section".into());
    }
    if first.legs.iter().any(|l| !(l.is_finite() && *l > 0.0))
        || first.directions.iter().any(|d| d.abs() != 1.0)
    {
        return Err("gusset-rib legs must be positive and directions signed".into());
    }
    ribs.sort_by(|a, b| (a.bounds[0] + a.bounds[1]).total_cmp(&(b.bounds[0] + b.bounds[1])));
    let thickness = first.bounds[1] - first.bounds[0];
    if ribs
        .iter()
        .any(|r| r.bounds[1] <= r.bounds[0] || (r.bounds[1] - r.bounds[0] - thickness).abs() > 1e-6)
    {
        return Err("a gusset-rib pattern must have one common thickness".into());
    }
    let centres: Vec<f64> = ribs
        .iter()
        .map(|r| (r.bounds[0] + r.bounds[1]) / 2.0)
        .collect();
    if centres.windows(2).any(|w| w[1] <= w[0]) {
        return Err("gusset-rib members must be ordered along the thickness axis".into());
    }
    let valid = match &arrangement {
        Arrangement::Single => ribs.len() == 1,
        Arrangement::Linear { pitch } => {
            ribs.len() >= 2
                && *pitch > 0.0
                && centres
                    .windows(2)
                    .all(|w| (w[1] - w[0] - pitch).abs() <= 1e-6)
        }
        Arrangement::Mirror { axis, plane } => {
            ribs.len() == 2
                && *axis == first.axis
                && ((centres[0] + centres[1]) / 2.0 - plane).abs() <= 1e-6
        }
    };
    if !valid {
        return Err("gusset-rib pattern facts are inconsistent".into());
    }
    Ok((ribs, arrangement))
}

/// The Analysis's gusset groups: each pattern, then each rib in no pattern.
pub fn groups(analysis: &Analysis) -> Vec<Group<'_>> {
    let frame = frame(analysis);
    let ribs: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "gusset_rib")
        .collect();
    let mut taken = vec![false; ribs.len()];
    let mut out = Vec::new();
    for p in analysis
        .features
        .iter()
        .filter(|f| f.kind == "gusset_rib_pattern")
    {
        let records: Vec<&Value> = p
            .parameters
            .get("ribs")
            .and_then(Value::as_array)
            .map(|a| a.iter().collect())
            .unwrap_or_default();
        let mut members = Vec::new();
        for r in &records {
            match ribs
                .iter()
                .position(|f| facts(&Value::Object(f.parameters.clone())) == facts(r))
            {
                Some(i) => {
                    taken[i] = true;
                    members.push(ribs[i]);
                }
                None => members.push(p),
            }
        }
        let arrangement = if let Some(pitch) = p.parameters.get("pitch").and_then(Value::as_f64) {
            Ok(Arrangement::Linear { pitch })
        } else {
            p.parameters
                .get("mirror_plane")
                .and_then(Value::as_array)
                .and_then(|m| {
                    let (k, s) = axis(&frame, m.first())?;
                    Some(Arrangement::Mirror {
                        axis: k,
                        plane: frame[0][k] + s * m.get(1)?.as_f64()?,
                    })
                })
                .ok_or("its arrangement is unreadable".to_string())
        };
        let read = if records.is_empty() {
            Err("its record lists no ribs".to_string())
        } else if !in_order(&records) {
            Err("gusset-rib members must be ordered along the thickness axis".to_string())
        } else {
            records
                .iter()
                .map(|r| rib(&frame, r))
                .collect::<Result<Vec<_>, _>>()
                .and_then(|ribs| check(ribs, arrangement?))
        };
        if records.is_empty() {
            members.push(p);
        }
        out.push(Group {
            owner: p,
            members,
            read,
        });
    }
    for (f, _) in ribs.iter().zip(&taken).filter(|(_, t)| !**t) {
        let read = rib(&frame, &Value::Object(f.parameters.clone()))
            .and_then(|r| check(vec![r], Arrangement::Single));
        out.push(Group {
            owner: f,
            members: vec![f],
            read,
        });
    }
    out
}

/// One requirement of a group: the feature it is about, the measurement and its value, or why it
/// cannot be checked.
pub struct Wanted<'a> {
    pub subject: &'a Feature,
    pub owner: &'a Feature,
    pub parameter: String,
    pub value: Result<f64, String>,
}

/// The gusset groups' requirements, in draftwright's parameter order.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for g in groups(analysis) {
        let (ribs, arrangement) = match &g.read {
            Ok(read) => read,
            Err(why) => {
                out.extend(g.members.iter().map(|m| Wanted {
                    subject: m,
                    owner: g.owner,
                    parameter: "gusset_thickness.length".into(),
                    value: Err(why.clone()),
                }));
                continue;
            }
        };
        let first = &ribs[0];
        for m in &g.members {
            out.push(Wanted {
                subject: m,
                owner: g.owner,
                parameter: "gusset_thickness.length".into(),
                value: Ok(first.bounds[1] - first.bounds[0]),
            });
            for (i, (k, _)) in first.supports.iter().enumerate() {
                out.push(Wanted {
                    subject: m,
                    owner: g.owner,
                    parameter: format!("gusset_leg.length.{}", AXES[*k]),
                    value: Ok(first.legs[i]),
                });
            }
        }
        let centres: Vec<f64> = ribs
            .iter()
            .map(|r| (r.bounds[0] + r.bounds[1]) / 2.0)
            .collect();
        match arrangement {
            Arrangement::Linear { pitch } => out.push(Wanted {
                subject: g.owner,
                owner: g.owner,
                parameter: "gusset_pitch.length".into(),
                value: Ok(*pitch),
            }),
            Arrangement::Mirror { .. } => out.push(Wanted {
                subject: g.owner,
                owner: g.owner,
                parameter: "gusset_spacing.length".into(),
                value: Ok((centres[1] - centres[0]).abs()),
            }),
            Arrangement::Single => {}
        }
        let target = match arrangement {
            Arrangement::Mirror { plane, .. } => *plane,
            _ => centres[0],
        };
        out.push(Wanted {
            subject: g.owner,
            owner: g.owner,
            parameter: "gusset_location.length".into(),
            value: Ok((target - analysis.envelope[0][first.axis]).abs()),
        });
    }
    out
}

/// The gusset rib findings, each `gusset_rib_requirement_<state>`.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for w in wanted(analysis) {
        let (subject, owner) = (&w.subject.id, &w.owner.id);
        let key = format!("{subject}/{}", w.parameter);
        // A pattern member with no rib feature of its own is counted by the pattern: number it.
        let n = seen.iter().filter(|s| **s == key).count();
        seen.push(key.clone());
        let id_tail = if n == 0 {
            key
        } else {
            format!("{key}#{}", n + 1)
        };
        let claiming: Vec<&Mark> = marks
            .iter()
            .filter(|m| {
                m.claims
                    .iter()
                    .any(|c| &c.feature == owner && c.parameter == w.parameter)
            })
            .collect();
        let noun = if subject == owner {
            format!("gusset rib {owner}")
        } else {
            format!("gusset rib {subject} of {owner}")
        };
        let issue =
            |state: &str, message: String, evidence: Option<String>, fix: Option<String>| {
                let code = format!("gusset_rib_requirement_{state}");
                LintIssue {
                    sheet: None,
                    id: format!("l:{code}:{id_tail}"),
                    code,
                    severity: Severity::Warning,
                    message,
                    at: None,
                    view: claiming.first().and_then(|m| m.view.clone()),
                    subjects: vec![subject.clone()],
                    requirements: vec![Requirement {
                        feature: owner.clone(),
                        parameter: w.parameter.clone(),
                    }],
                    measurements: vec![format!("{owner}/{}", w.parameter)],
                    sources: Vec::new(),
                    stage: None,
                    annotation: claiming.first().map(|m| m.name.clone()),
                    related: claiming.iter().skip(1).map(|m| m.name.clone()).collect(),
                    evidence,
                    fix,
                }
            };
        let value = match &w.value {
            Ok(v) => *v,
            Err(why) => {
                issues.push(issue(
                    "unverifiable",
                    format!(
                        "{noun}: requirement {} cannot be checked: {why}",
                        w.parameter
                    ),
                    Some(why.clone()),
                    None,
                ));
                continue;
            }
        };
        if claiming
            .iter()
            .any(|m| holes::states_in(&m.text, value, m.units))
        {
            continue;
        }
        let fix = Some(format!(
            "State {} for the ribs: the gusset callout (thickness, legs, pitch or spacing, and \
             location), in the view that sees the ribs' triangles.",
            fmt(value)
        ));
        issues.push(match claiming.first() {
            Some(m) => issue(
                "unverifiable",
                format!(
                    "{noun}: requirement {} is claimed by {} but its text does not state {}",
                    w.parameter,
                    m.name,
                    fmt(value)
                ),
                Some(format!("{:?} does not state {}", m.text, fmt(value))),
                fix,
            ),
            None => issue(
                "missing",
                format!(
                    "{noun}: requirement {} ({}) is not stated on the sheet",
                    w.parameter,
                    fmt(value)
                ),
                None,
                fix,
            ),
        });
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    /// test_gusset_rib_semantics's ribs: 6 thick across x, standing on the planes y = 17 and
    /// z = 8, legs 22 (towards -y) and 26 (towards +z).
    fn rib(lo: f64) -> Value {
        json!({ "thickness_axis": "x", "thickness_bounds": [lo, lo + 6.0],
                "supports": [["y", 17.0], ["z", 8.0]], "legs": [22.0, 26.0],
                "directions": [-1, 1], "body_key": [1.0] })
    }

    /// The bracket's ribs at *los*, with a pattern of them (its record) when given.
    fn bracket(los: &[f64], pattern: Option<Value>, x_min: f64) -> Analysis {
        let mut a = part("gusset_rib", rib(los[0]));
        for (i, lo) in los.iter().enumerate().skip(1) {
            let mut f = a.features[0].clone();
            f.id = format!("f:gusset_rib:{}", i + 1);
            f.faces = vec![i];
            f.parameters = rib(*lo).as_object().unwrap().clone();
            a.features.push(f);
        }
        if let Some(mut p) = pattern {
            p["ribs"] = json!(los.iter().map(|lo| rib(*lo)).collect::<Vec<_>>());
            let mut f = a.features[0].clone();
            f.id = "f:gusset_rib_pattern:1".into();
            f.kind = "gusset_rib_pattern".into();
            f.faces = (0..los.len()).collect();
            f.parameters = p.as_object().unwrap().clone();
            a.features.push(f);
        }
        a.envelope = [[x_min, -25.0, 0.0], [-x_min, 25.0, 48.0]];
        a
    }

    fn callout(text: &str, parameters: &[&str]) -> Mark {
        Mark {
            units: Default::default(),
            name: "m_gusset_rib_x0".into(),
            view: Some("v:side".into()),
            text: text.into(),
            claims: parameters
                .iter()
                .map(|p| Claim {
                    feature: "f:gusset_rib_pattern:1".into(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    /// test_gusset_rib_semantics (`test_gusset_ribs_and_mirror_relation_reach_same_run_consumer_policy`):
    /// the mirrored pair's eight requirements (thickness and two legs for each rib, spacing,
    /// location) are all stated by draftwright's callout `2× GUSSET 6 THK · LEGS 22 × 26 · 58 C/C
    /// MIRROR · MIRROR PLANE 40 FROM X MIN`; without it each is missing.
    #[test]
    fn a_mirrored_gusset_pair_states_its_ribs_spacing_and_plane() {
        let a = bracket(
            &[-32.0, 26.0],
            Some(json!({ "mirror_plane": ["x", 0.0] })),
            -40.0,
        );
        let w = wanted(&a);
        let got: Vec<(String, String, String)> = w
            .iter()
            .map(|w| {
                (
                    w.subject.id.clone(),
                    w.parameter.clone(),
                    fmt(*w.value.as_ref().unwrap()),
                )
            })
            .collect();
        let row = |s: &str, p: &str, v: &str| (s.to_string(), p.to_string(), v.to_string());
        assert_eq!(
            got,
            [
                row("f:gusset_rib:1", "gusset_thickness.length", "6"),
                row("f:gusset_rib:1", "gusset_leg.length.y", "22"),
                row("f:gusset_rib:1", "gusset_leg.length.z", "26"),
                row("f:gusset_rib:2", "gusset_thickness.length", "6"),
                row("f:gusset_rib:2", "gusset_leg.length.y", "22"),
                row("f:gusset_rib:2", "gusset_leg.length.z", "26"),
                row("f:gusset_rib_pattern:1", "gusset_spacing.length", "58"),
                row("f:gusset_rib_pattern:1", "gusset_location.length", "40"),
            ]
        );
        let all = [
            "gusset_thickness.length",
            "gusset_leg.length.y",
            "gusset_leg.length.z",
            "gusset_spacing.length",
            "gusset_location.length",
        ];
        let label = "2× GUSSET 6 THK · LEGS 22 × 26 · 58 C/C MIRROR · MIRROR PLANE 40 FROM X MIN";
        assert!(coverage(&a, &[callout(label, &all)]).is_empty());
        let missing = coverage(&a, &[]);
        assert_eq!(missing.len(), 8);
        assert!(
            missing
                .iter()
                .all(|i| i.code == "gusset_rib_requirement_missing")
        );
        let ids: std::collections::BTreeSet<&String> = missing.iter().map(|i| &i.id).collect();
        assert_eq!(ids.len(), 8, "every finding has its own ID");
    }

    /// `test_linear_gusset_array_keeps_provider_pitch_and_member_ownership`: three ribs at a
    /// 25 pitch, the first's centre 17 from the box (`3× GUSSET 6 THK · LEGS 22 × 26 · 25 PITCH ·
    /// FIRST CL 17 FROM X MIN`), eleven requirements; a lone rib is its own group; a pattern whose
    /// centres are not one pitch apart has no feature in draftwright, so each of its ribs is
    /// unverifiable (`test_declared_gusset_patterns_reject_false_relationships`), and so is one whose
    /// members quiddity lists out of order along the thickness axis (`GussetRibFeature` refuses it).
    #[test]
    fn a_gusset_array_states_its_pitch_and_first_centre() {
        let a = bracket(
            &[-36.0, -11.0, 14.0],
            Some(json!({ "axis": "x", "pitch": 25.0 })),
            -50.0,
        );
        assert_eq!(wanted(&a).len(), 11);
        let all = [
            "gusset_thickness.length",
            "gusset_leg.length.y",
            "gusset_leg.length.z",
            "gusset_pitch.length",
            "gusset_location.length",
        ];
        let label = "3× GUSSET 6 THK · LEGS 22 × 26 · 25 PITCH · FIRST CL 17 FROM X MIN";
        assert!(coverage(&a, &[callout(label, &all)]).is_empty());
        let wrong = coverage(&a, &[callout(&label.replace("17", "18"), &all)]);
        assert_eq!(
            wrong
                .iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect::<Vec<_>>(),
            ["gusset_rib_requirement_unverifiable gusset_location.length"]
        );
        let lone = bracket(&[-36.0], None, -50.0);
        assert_eq!(wanted(&lone).len(), 4);
        let skewed = bracket(
            &[-3.0, 7.0, 24.0],
            Some(json!({ "axis": "x", "pitch": 10.0 })),
            -40.0,
        );
        let shuffled = bracket(
            &[-11.0, -36.0, 14.0],
            Some(json!({ "axis": "x", "pitch": 25.0 })),
            -50.0,
        );
        let refused = coverage(&shuffled, &[callout(label, &all)]);
        assert_eq!(refused.len(), 3);
        assert!(refused.iter().all(|i| i.evidence.as_deref()
            == Some("gusset-rib members must be ordered along the thickness axis")));
        let found = coverage(&skewed, &[]);
        assert_eq!(found.len(), 3);
        assert!(
            found
                .iter()
                .all(|i| i.code == "gusset_rib_requirement_unverifiable"
                    && i.requirements[0].parameter == "gusset_thickness.length"
                    && i.evidence.as_deref() == Some("gusset-rib pattern facts are inconsistent"))
        );
    }
}
