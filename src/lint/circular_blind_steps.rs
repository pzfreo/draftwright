//! Circular blind step coverage (Python's `linting/circular_blind_step_coverage.py`): a
//! quarter-cylinder blind step states its radius and its blind depth
//! (LINT-circular_blind_step_coverage-radius_depth,
//! LINT-code-circular_blind_step_requirement_state).
//!
//! Each of the Analysis's `circular_blind_step` features (quiddity's `CircularBlindStep`: its run
//! `axis`, `radius`, the blind `length`, its `centreline` from the blind terminal to the open
//! end, and its `section`) has two requirements, `circular_step_radius.radius` and
//! `circular_step_depth.length`, each judged by [`parameters::coverage`]: an annotation that
//! claims it must print the value. Python validates the record's centreline and section against
//! its radius and length only to join it to its own IR feature; here the Analysis feature is the
//! record, so there is no join to fail, and a record without a radius or length has that
//! requirement reported unverifiable.

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::frame;
use crate::model::Analysis;

/// The circular blind steps' requirements: the radius, then the depth, of each.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "circular_blind_step")
    {
        let radius = parameters::size(f, "radius");
        out.push(Wanted {
            feature: f,
            parameter: "circular_step_radius.radius".into(),
            callout: radius
                .as_ref()
                .map_or("R?".into(), |r| format!("R{}", fmt(*r))),
            values: radius.map(|r| vec![r]),
        });
        let depth = parameters::size(f, "length");
        out.push(Wanted {
            feature: f,
            parameter: "circular_step_depth.length".into(),
            callout: depth
                .as_ref()
                .map_or("its depth".into(), |l| format!("its depth {}", fmt(*l))),
            values: depth.map(|l| vec![l]),
        });
    }
    out
}

/// The circular blind step coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("circular blind step {}", f.id);
        if let Ok(r) = parameters::size(f, "radius") {
            s += &format!(" R{}", fmt(r));
        }
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            s += &format!(" along {}", fmt3(a));
        }
        let terminal = p
            .get("centreline")
            .and_then(|c| c.as_array())
            .and_then(|c| c.first());
        if let Some(at) = parameters::place(&frame, terminal) {
            s += &format!(", blind end at {}", fmt3(at));
        }
        s
    };
    parameters::coverage(
        "circular_blind_step",
        &wanted(analysis),
        marks,
        noun,
        "a radius callout and a linear dimension of the depth, in views that see the arc and \
         the run",
    )
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    /// A circular blind step's radius and depth are each stated by a claiming annotation's
    /// text; unclaimed, both are missing; a record without its length cannot be checked.
    #[test]
    fn a_circular_blind_step_states_its_radius_and_depth() {
        let record = json!({ "axis": "z", "radius": 4.0, "length": 9.0,
                             "centreline": [[0.0, 0.0, 1.0], [0.0, 0.0, 10.0]],
                             "section": [[0.0, 4.0], [0.0, 0.0], [4.0, 0.0]] });
        let a = part("circular_blind_step", record.clone());
        let mark = |text: &str, parameter: &str| Mark {
            name: format!("m:{parameter}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: "f:circular_blind_step:1".into(),
                parameter: parameter.into(),
            }],
        };
        assert!(
            coverage(
                &a,
                &[
                    mark("R4", "circular_step_radius.radius"),
                    mark("9", "circular_step_depth.length")
                ]
            )
            .is_empty()
        );
        let codes = |a: &Analysis, marks: &[Mark]| -> Vec<String> {
            coverage(a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        assert_eq!(
            codes(&a, &[]),
            [
                "circular_blind_step_requirement_missing circular_step_radius.radius",
                "circular_blind_step_requirement_missing circular_step_depth.length"
            ]
        );
        let mut bad = record;
        bad.as_object_mut().unwrap().remove("length");
        assert_eq!(
            codes(
                &part("circular_blind_step", bad),
                &[mark("R4", "circular_step_radius.radius")]
            ),
            ["circular_blind_step_requirement_unverifiable circular_step_depth.length"]
        );
    }
}
