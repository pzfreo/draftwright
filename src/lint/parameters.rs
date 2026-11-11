//! Fixed-parameter coverage (Python's `linting/_parameter_coverage.py`), shared by the families
//! whose requirements are a feature's own sizes: chamfers, fillets, blends, through steps, slots
//! and bosses. Each family reads its features from the Analysis into [`Wanted`] requirements; this
//! module judges them against the marks, as hole coverage does
//! (LINT-_parameter_coverage-outcome_states, -report_uncovered_only):
//!
//! - **placed** (nothing reported): an annotation claims the feature's parameter and its printed
//!   text states every value the requirement names, at the precision printed
//!   ([`holes::states`]);
//! - **unverifiable**: an annotation claims it but its text does not state a value, or the
//!   Analysis does not give the value (the feature's record lacks it);
//! - **missing**: no annotation claims it.
//!
//! The code is `<family>_requirement_<state>` and the severity a warning (the Analysis does not say
//! whether the part is several bodies, so the assembly severity is not reached). Lint cannot see
//! the author's omissions or placement drops, so `suppressed` and `dropped` are not reached here.

use serde_json::Value;

use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, point, to_part};

use super::holes;
use super::{LintIssue, Mark, Requirement, Severity};

/// One requirement of one feature, as its family reads it from the Analysis.
#[derive(Clone, Debug, PartialEq)]
pub struct Wanted<'a> {
    pub feature: &'a Feature,
    /// In the role vocabulary, e.g. `fillet.radius`.
    pub parameter: String,
    /// The values the claiming annotation's text must state (a chamfer's leg, and its angle unless
    /// it is 45° with equal legs), or why the Analysis does not give them.
    pub values: Result<Vec<f64>, String>,
    /// What the callout stating it reads, for the fix (`R3`, `C1`, `4.5 × 42.1°`).
    pub callout: String,
}

/// A feature's requirements that another feature's dimensions state in its place (#233): each
/// requirement, with the stating feature's ID and the parameter its dimension measures.
pub type Owners = Vec<(&'static str, String, &'static str)>;

/// *marks* that claim an owner's parameter, again as claims of the requirement it states for
/// *f*: the owner's printed value is checked against *f*'s own.
pub fn relay(marks: &[Mark], f: &Feature, owners: &Owners) -> Vec<Mark> {
    let mut out = Vec::new();
    for (requirement, owner, parameter) in owners {
        out.extend(
            marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|c| &c.feature == owner && c.parameter == *parameter)
                })
                .map(|m| Mark {
                    claims: vec![crate::output::Claim {
                        feature: f.id.clone(),
                        parameter: (*requirement).into(),
                    }],
                    ..m.clone()
                }),
        );
    }
    out
}

/// A record's direction (an axis letter or a vector, in quiddity's frame) in the part's frame.
pub fn direction(frame: &[[f64; 3]; 4], v: Option<&Value>) -> Option<[f64; 3]> {
    let local = match v? {
        Value::String(s) if s == "x" => [1.0, 0.0, 0.0],
        Value::String(s) if s == "y" => [0.0, 1.0, 0.0],
        Value::String(s) if s == "z" => [0.0, 0.0, 1.0],
        v => point(Some(v))?,
    };
    Some(dir_to_part(frame, local))
}

/// A record's point (in quiddity's frame) in the part's frame.
pub fn place(frame: &[[f64; 3]; 4], v: Option<&Value>) -> Option<[f64; 3]> {
    point(v).map(|l| to_part(frame, l))
}

/// A record's number field *name*, or why it cannot be read.
pub fn size(f: &Feature, name: &str) -> Result<f64, String> {
    f.parameters
        .get(name)
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("its record gives no {name}"))
}

/// A number as messages print it: at most three decimals, trailing zeros dropped.
pub fn fmt(v: f64) -> String {
    let v = (v * 1e6).round() / 1e6;
    let places = (0..3)
        .find(|&places| {
            let factor = 10_f64.powi(places as i32);
            ((v * factor).round() / factor - v).abs() <= 1e-6 + 1e-12
        })
        .unwrap_or(3);
    let text = format!("{v:.places$}");
    if places == 3
        && text
            .parse::<f64>()
            .is_ok_and(|n| (n - v).abs() > 1e-6 + 1e-12)
    {
        return if text.parse::<f64>() == Ok(0.) {
            "0.000".into()
        } else {
            text
        };
    }
    let text = if places == 0 {
        text.as_str()
    } else {
        text.trim_end_matches('0').trim_end_matches('.')
    };
    if text == "-0" {
        "0".into()
    } else {
        text.into()
    }
}

/// A point or direction as messages print it.
pub fn fmt3(p: [f64; 3]) -> String {
    format!("({})", p.map(fmt).join(", "))
}

/// A feature's drawing noun, using only recognition evidence available to lint.
pub(crate) fn feature_name(feature: &Feature) -> String {
    format!("the {}", feature.kind.replace('_', " "))
}

/// The findings of *family* (`fillet`, `chamfer`, …) on *wanted*: each requirement the marks do not
/// state. *noun* describes a feature in messages (`fillet f:fillet:3, R3, …`); *how* says what
/// states it and where (`a radius callout, in a view that sees its arc`), for the fix.
pub fn coverage(
    family: &str,
    wanted: &[Wanted],
    marks: &[Mark],
    noun: impl Fn(&Feature) -> String,
    how: &str,
) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    for w in wanted {
        let id = &w.feature.id;
        let parameter = w.parameter.as_str();
        let claiming: Vec<&Mark> = marks
            .iter()
            .filter(|m| {
                m.claims
                    .iter()
                    .any(|c| &c.feature == id && c.parameter == parameter)
            })
            .collect();
        let issue =
            |state: &str, message: String, evidence: Option<String>, fix: Option<String>| {
                let code = format!("{family}_requirement_{state}");
                LintIssue {
                    sheet: None,
                    id: format!("l:{code}:{id}/{parameter}"),
                    code,
                    severity: Severity::Warning,
                    message,
                    at: None,
                    view: claiming.first().and_then(|m| m.view.clone()),
                    subjects: vec![id.clone()],
                    requirements: vec![Requirement {
                        feature: id.clone(),
                        parameter: parameter.into(),
                    }],
                    measurements: vec![format!("{id}/{parameter}")],
                    sources: Vec::new(),
                    stage: None,
                    annotation: claiming.first().map(|m| m.name.clone()),
                    related: claiming.iter().skip(1).map(|m| m.name.clone()).collect(),
                    evidence,
                    fix,
                }
            };
        let values = match &w.values {
            Ok(v) => v,
            Err(why) => {
                issues.push(issue(
                    "unverifiable",
                    format!(
                        "{}: requirement {parameter} cannot be checked: the analysis does not give its value",
                        noun(w.feature)
                    ),
                    Some(why.clone()),
                    None,
                ));
                continue;
            }
        };
        let states = |m: &&Mark| {
            values.iter().all(|&v| {
                holes::states_in(
                    &m.text,
                    v,
                    if parameter.ends_with(".count") {
                        crate::output::Units::Mm
                    } else {
                        m.units
                    },
                )
            })
        };
        if claiming.iter().any(states) {
            continue;
        }
        let fix = Some(format!(
            "State {} for {}: {how}.",
            w.callout,
            feature_name(w.feature)
        ));
        issues.push(match claiming.first() {
            Some(m) => issue(
                "unverifiable",
                format!(
                    "{}: requirement {parameter} is claimed by {} but its text does not state {}",
                    noun(w.feature),
                    m.name,
                    w.callout
                ),
                Some(format!("{:?} does not state {}", m.text, w.callout)),
                fix,
            ),
            None => issue(
                "missing",
                format!(
                    "{}: requirement {parameter} ({}) is not stated on the sheet",
                    noun(w.feature),
                    w.callout
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
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn feature() -> Feature {
        serde_json::from_value(json!({
            "id": "f:fillet:1", "kind": "fillet", "faces": [0], "parameters": {},
            "fingerprint": { "version": "test", "family": "fillets", "subtype": "Fillet",
                             "sizes": {}, "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
        }))
        .unwrap()
    }

    fn mark(text: &str, parameter: &str) -> Mark {
        Mark {
            units: Default::default(),
            name: format!("m:{text}"),
            view: Some("v:front".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: "f:fillet:1".into(),
                parameter: parameter.into(),
            }],
        }
    }

    /// Placed when claimed and stated; unverifiable when claimed but misprinted, or when the
    /// Analysis gives no value; missing when unclaimed. A claim of another parameter is no claim.
    #[test]
    fn each_requirement_resolves_to_one_outcome() {
        let f = feature();
        let wanted = |values: Result<Vec<f64>, String>| {
            vec![Wanted {
                feature: &f,
                parameter: "fillet.radius".into(),
                values,
                callout: "R3".into(),
            }]
        };
        let found = |values: Result<Vec<f64>, String>, marks: &[Mark]| -> Vec<String> {
            coverage(
                "fillet",
                &wanted(values),
                marks,
                |f| f.id.clone(),
                "a radius callout, in a view that sees its arc",
            )
            .into_iter()
            .map(|i| i.code)
            .collect()
        };
        let placed = mark("2× R3", "fillet.radius");
        assert!(found(Ok(vec![3.0]), std::slice::from_ref(&placed)).is_empty());
        assert_eq!(
            found(Ok(vec![3.0]), &[mark("R4", "fillet.radius")]),
            ["fillet_requirement_unverifiable"]
        );
        assert_eq!(
            found(Ok(vec![3.0]), &[mark("R3", "fillet.depth")]),
            ["fillet_requirement_missing"]
        );
        assert_eq!(
            found(Err("its record gives no radius".into()), &[placed]),
            ["fillet_requirement_unverifiable"]
        );
        // Every value must be stated: a leg without its angle is not.
        assert_eq!(
            found(Ok(vec![4.47, 42.11]), &[mark("4.5", "fillet.radius")]),
            ["fillet_requirement_unverifiable"]
        );
        assert!(
            found(
                Ok(vec![4.47, 42.11]),
                &[mark("4.5 × 42.1°", "fillet.radius")]
            )
            .is_empty()
        );
        let issue = &coverage(
            "fillet",
            &wanted(Ok(vec![3.0])),
            &[],
            |f| f.id.clone(),
            "a radius callout, in a view that sees its arc",
        )[0];
        assert_eq!(
            issue.id,
            "l:fillet_requirement_missing:f:fillet:1/fillet.radius"
        );
        assert_eq!(
            issue.fix.as_deref(),
            Some("State R3 for the fillet: a radius callout, in a view that sees its arc.")
        );
    }
}
