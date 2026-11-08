//! Paired-ramp step coverage (Python's `linting/paired_ramp_step_coverage.py`): a paired ramp
//! step states its (equal) ramp angle and its open-to-terminal run
//! (LINT-paired_ramp_step_coverage-angle_run, LINT-code-paired_ramp_step_requirement_state).
//!
//! Each of the Analysis's `paired_ramp_step` features (quiddity's `PairedRampStep`: its run
//! `axis`, either ramp's `angle` in degrees, the run `length`, and `at`, the midpoint of the
//! ramps' shared ridge) has two requirements, `ramp_angle.angle` and `ramp_run.length`, each
//! judged by [`parameters::coverage`]: an annotation that claims it must print the value. Python
//! joins quiddity's record to its own IR feature by exact geometry first; here the Analysis
//! feature is the record, so there is no join to fail.

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::frame;
use crate::model::Analysis;

/// The paired ramp steps' requirements: the angle, then the run, of each.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "paired_ramp_step")
    {
        let angle = parameters::size(f, "angle");
        out.push(Wanted {
            feature: f,
            parameter: "ramp_angle.angle".into(),
            callout: angle
                .as_ref()
                .map_or("its ramp angle".into(), |a| format!("{}°", fmt(*a))),
            values: angle.map(|a| vec![a]),
        });
        let run = parameters::size(f, "length");
        out.push(Wanted {
            feature: f,
            parameter: "ramp_run.length".into(),
            callout: run
                .as_ref()
                .map_or("its run".into(), |l| format!("its run {}", fmt(*l))),
            values: run.map(|l| vec![l]),
        });
    }
    out
}

/// The paired ramp step coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("paired-ramp step {}", f.id);
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            s += &format!(" along {}", fmt3(a));
        }
        if let Some(at) = parameters::place(&frame, p.get("at")) {
            s += &format!(", ridge at {}", fmt3(at));
        }
        s
    };
    parameters::coverage(
        "paired_ramp_step",
        &wanted(analysis),
        marks,
        noun,
        "an angular dimension of a ramp and a linear dimension of the run, in a view that sees \
         the ramps' section",
    )
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::{Value, json};

    pub(crate) fn part(kind: &str, record: Value) -> Analysis {
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 2, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [100.0, 100.0, 10.0]],
            "features": [{
                "id": format!("f:{kind}:1"), "kind": kind, "faces": [0],
                "parameters": record,
                "fingerprint": { "version": "test", "family": "test", "subtype": "test",
                                 "sizes": {}, "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
            }],
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
            "recognition": { "recogniser": "test",
                             "frame": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]] },
        }))
        .unwrap()
    }

    /// A paired ramp's angle and run are each stated by a claiming annotation's text; a run
    /// left unstated is missing, a misprinted angle unverifiable.
    #[test]
    fn a_paired_ramp_step_states_its_angle_and_run() {
        let a = part(
            "paired_ramp_step",
            json!({ "axis": "x", "angle": 30.0, "length": 12.5, "at": [5.0, 0.0, 8.0] }),
        );
        let mark = |name: &str, text: &str, parameter: &str| Mark {
            name: name.into(),
            view: Some("v:front".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: "f:paired_ramp_step:1".into(),
                parameter: parameter.into(),
            }],
        };
        let angle = mark("m_ramp_angle", "30°", "ramp_angle.angle");
        let run = mark("m_ramp_run", "12.5", "ramp_run.length");
        assert!(coverage(&a, &[angle.clone(), run]).is_empty());
        let codes = |marks: &[Mark]| -> Vec<String> {
            coverage(&a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        assert_eq!(
            codes(&[angle]),
            ["paired_ramp_step_requirement_missing ramp_run.length"]
        );
        assert_eq!(
            codes(&[
                mark("m_ramp_angle", "35°", "ramp_angle.angle"),
                mark("m_ramp_run", "12.5", "ramp_run.length")
            ]),
            ["paired_ramp_step_requirement_unverifiable ramp_angle.angle"]
        );
    }
}
