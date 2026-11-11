//! Chamfer coverage (Python's `linting/chamfer_coverage.py`): every recognised chamfer states its
//! size (LINT-chamfer_coverage-size, LINT-code-chamfer_requirement_state).
//!
//! Each of the Analysis's `chamfer` features (quiddity's `Chamfer`: `leg1`, `leg2`, `angle`,
//! `axis`, `at`) has one requirement, `chamfer.length`, its first leg, judged by
//! [`parameters::coverage`]. Its callout is draftwright's (`_geometry._fmt_chamfer`): `C<leg>`
//! for legs within 0.05 mm of each other at an angle within 0.5° of 45°, otherwise
//! `<leg> × <angle>°`. So the claiming annotation must print the leg, and for the second form the
//! angle too: a leg alone does not size an unequal bevel. (Python's lint follows the measurement's identity alone;
//! printing the angle is the port's reading of what the callout must state.)
//!
//! quiddity's `OrientedChamfer` (a bevel along an oblique edge) is another family, which Python's
//! lint does not judge either. Rust reads it with the same true-section callout grammar,
//! using `oriented_chamfer.length` (and an angle rider for unequal legs).

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::frame;
use crate::model::Analysis;

/// Whether a chamfer of these sizes is called out `C<leg>`: draftwright's rule.
pub fn equal_45(leg1: f64, leg2: f64, angle: f64) -> bool {
    (leg1 - leg2).abs() < 0.05 && (angle - 45.0).abs() < 0.5
}

/// The chamfers' requirements.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    analysis
        .features
        .iter()
        .filter(|f| matches!(f.kind.as_str(), "chamfer" | "oriented_chamfer"))
        .map(|f| {
            let sizes = (|| {
                if f.kind == "oriented_chamfer" {
                    super::oriented_chamfers::read(analysis, f)?;
                }
                let leg1 = parameters::size(f, "leg1")?;
                let leg2 = parameters::size(f, "leg2")?;
                let angle = parameters::size(f, "angle")?;
                Ok::<_, String>((leg1, leg2, angle))
            })();
            let (values, callout) = match sizes {
                Ok((leg, other, angle)) if equal_45(leg, other, angle) => {
                    (Ok(vec![leg]), format!("C{}", fmt(leg)))
                }
                Ok((leg, _, angle)) => (
                    Ok(vec![leg, angle]),
                    format!("{} × {}°", fmt(leg), fmt(angle)),
                ),
                Err(why) => (Err(why), "its size".into()),
            };
            Wanted {
                feature: f,
                parameter: if f.kind == "oriented_chamfer" {
                    "oriented_chamfer.length"
                } else {
                    "chamfer.length"
                }
                .into(),
                values,
                callout,
            }
        })
        .collect()
}

/// The chamfer coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("chamfer {}", f.id);
        if let (Ok(a), Ok(b)) = (parameters::size(f, "leg1"), parameters::size(f, "leg2")) {
            s += &format!(", legs {} × {}", fmt(a), fmt(b));
        }
        if let Ok(angle) = parameters::size(f, "angle") {
            s += &format!(", {}°", fmt(angle));
        }
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            let turned = p.get("turned").and_then(|v| v.as_bool()) == Some(true);
            s += &format!(", {} {}", if turned { "about" } else { "along" }, fmt3(a));
        }
        if let Some(at) = parameters::place(&frame, p.get("at")) {
            s += &format!(", at {}", fmt3(at));
        }
        s
    };
    // Only the nominal callout proves its size/angle; tolerance digits cannot
    // rescue a false bevel value. Wording is already stripped by lint::marks.
    let nominal: Vec<_> = marks
        .iter()
        .cloned()
        .map(|mut m| {
            m.text = m.text.split(['±', '+']).next().unwrap_or("").to_string();
            if let Some((before, _)) = m.text.split_once(" -") {
                m.text = before.to_string();
            }
            m
        })
        .collect();
    parameters::coverage(
        "chamfer",
        &wanted(analysis),
        &nominal,
        noun,
        "a chamfer callout, in a view that sees the bevel edge-on",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn part(records: &[serde_json::Value]) -> Analysis {
        let features: Vec<serde_json::Value> = records
            .iter()
            .enumerate()
            .map(|(i, r)| {
                json!({
                    "id": format!("f:chamfer:{}", i + 1), "kind": "chamfer", "faces": [i],
                    "parameters": r,
                    "fingerprint": { "version": "test", "family": "chamfers",
                                     "subtype": "Chamfer", "sizes": {},
                                     "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
                })
            })
            .collect();
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": records.len(),
                         "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [100.0, 100.0, 10.0]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        }))
        .unwrap()
    }

    fn mark(text: &str, feature: &str) -> Mark {
        Mark {
            units: Default::default(),
            name: format!("m:{text}"),
            view: Some("v:front".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: feature.into(),
                parameter: "chamfer.length".into(),
            }],
        }
    }

    /// LINT-chamfer_coverage-size: an equal-leg 45° chamfer is stated by `C<leg>`; an unequal one
    /// needs its leg and its angle; an unclaimed one is missing.
    #[test]
    fn a_chamfer_states_its_leg_and_its_angle_unless_it_is_c_form() {
        let a = part(&[
            json!({ "leg1": 1.0, "leg2": 1.0, "angle": 45.0, "axis": "z",
                    "at": [0.0, 0.0, 0.0], "turned": true }),
            json!({ "leg1": 4.47, "leg2": 4.04, "angle": 42.11, "axis": "x",
                    "at": [1.0, 2.0, 3.0], "turned": false }),
            json!({ "leg1": 2.0, "leg2": 2.0, "angle": 45.0, "axis": "y",
                    "at": [5.0, 5.0, 5.0], "turned": false }),
        ]);
        let w = wanted(&a);
        assert_eq!(
            w.iter().map(|w| w.callout.as_str()).collect::<Vec<_>>(),
            ["C1", "4.47 × 42.11°", "C2"]
        );
        let found = |marks: &[Mark]| -> Vec<String> {
            coverage(&a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.subjects[0]))
                .collect()
        };
        let stated = [
            mark("16× C1", "f:chamfer:1"),
            mark("4.5 × 42.1°", "f:chamfer:2"),
        ];
        assert_eq!(found(&stated), ["chamfer_requirement_missing f:chamfer:3"]);
        let short = [mark("C1", "f:chamfer:1"), mark("4.5", "f:chamfer:2")];
        assert_eq!(
            found(&short),
            [
                "chamfer_requirement_unverifiable f:chamfer:2",
                "chamfer_requirement_missing f:chamfer:3"
            ]
        );
        let issue = &coverage(&a, &stated)[0];
        assert_eq!(
            issue.message,
            "chamfer f:chamfer:3, legs 2 × 2, 45°, along (0, 1, 0), at (5, 5, 5): requirement chamfer.length (C2) is not stated on the sheet"
        );
    }

    /// A chamfer whose record lacks a size cannot be checked: reported, never guessed.
    #[test]
    fn a_chamfer_without_its_sizes_is_unverifiable() {
        let a = part(&[json!({ "leg1": 1.0, "axis": "z", "at": [0.0, 0.0, 0.0] })]);
        let issues = coverage(&a, &[mark("C1", "f:chamfer:1")]);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].code, "chamfer_requirement_unverifiable");
        assert_eq!(
            issues[0].evidence.as_deref(),
            Some("its record gives no leg2")
        );
    }
}
