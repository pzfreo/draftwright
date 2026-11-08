//! Profiled bore coverage (Python's `linting/profiled_bore_coverage.py`): a through double-D bore
//! is called out with both its major diameter and its size across flats, and the callouts account
//! for every such bore (LINT-profiled_bore_coverage-double_d).
//!
//! Each of the Analysis's `double_d_bore` features (quiddity's `DoubleDBore`: its `axis` and
//! `location`, `major_diameter`, `across_flats`, `through`, and `flat_direction`, the flats'
//! normal) is keyed as Python keys it ([`key`]): the part's axis it runs along, whether it is
//! through, both sizes to 1e-3, and the flats' direction made canonical (unit, its first non-zero
//! component positive, to 1e-6), all in the part's frame. A bore is documented when one annotation
//! claims both its `bore.diameter` and its `profile_across_flats.length` and prints both sizes (a
//! diameter alone is not enough, as in Python, where only a compound callout carrying the A/F
//! covers a profile). Each key whose bores are not all documented is one
//! `profiled_bore_not_dimensioned` warning, with Python's message (`N through double-D bore(s)
//! require …, but placed callouts document H`).
//!
//! Python credits a callout to the bores at its owner's sites (within 0.5 mm); here a claim names
//! its bore. Not reached: Python's credit for a callout the placement dropped (already reported as
//! `callout_dropped`), which lint cannot see, and the assembly severity.

use std::collections::BTreeMap;

use serde_json::Value;

use super::{LintIssue, Mark, Severity, holes};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, point, size_key};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// A double-D bore's identity (Python's `profiled_bore_key`): its axis, whether it is through,
/// its major diameter and size across flats (1e-3 mm), and its flats' canonical direction (1e-6).
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Key {
    pub axis: usize,
    pub through: bool,
    pub major: i64,
    pub across: i64,
    pub direction: [i64; 3],
}

/// Python's `round(x, n)`.
fn round(x: f64, n: usize) -> f64 {
    format!("{x:.n$}").parse().unwrap_or(x)
}

/// *f*'s key and its two sizes, in the part's frame, or why it cannot be read.
pub fn key(analysis: &Analysis, f: &Feature) -> Result<(Key, f64, f64), String> {
    let frame = frame(analysis);
    let p = &f.parameters;
    let axis = point(p.get("axis")).ok_or("its record gives no axis")?;
    let axis = dir_to_part(&frame, axis);
    let axis = (0..3)
        .max_by(|&i, &j| axis[i].abs().total_cmp(&axis[j].abs()))
        // Invariant: The iterator is over the fixed three coordinate axes.
        .expect("three components");
    let size = |name: &str| {
        p.get(name)
            .and_then(Value::as_f64)
            .filter(|x| x.is_finite())
            .ok_or(format!("its record gives no {name}"))
    };
    let (major, across) = (size("major_diameter")?, size("across_flats")?);
    let through = p
        .get("through")
        .and_then(Value::as_bool)
        .ok_or("its record does not say whether it is through")?;
    let d = point(p.get("flat_direction")).ok_or("its record gives no flat direction")?;
    let d = dir_to_part(&frame, d);
    let norm = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
    if !norm.is_finite() || norm <= 1e-12 {
        return Err("its flat direction is not a finite non-zero vector".into());
    }
    let mut d = d.map(|c| c / norm);
    if d.iter().find(|c| c.abs() > 1e-12).is_some_and(|c| *c < 0.0) {
        d = d.map(|c| -c);
    }
    let direction = d.map(|c| {
        if c.abs() <= 1e-12 {
            0
        } else {
            size_key(round(c, 6))
        }
    });
    Ok((
        Key {
            axis,
            through,
            major: size_key(round(major, 3)),
            across: size_key(round(across, 3)),
            direction,
        },
        major,
        across,
    ))
}

/// Use lint's independent live nominal policy for text comparisons.
fn short(v: f64) -> String {
    super::parameters::fmt(v)
}

/// Whether one of *marks* claims both of *f*'s sizes and prints both.
fn documented(f: &Feature, major: f64, across: f64, marks: &[Mark]) -> bool {
    marks.iter().any(|m| {
        let claims = |parameter: &str| {
            m.claims
                .iter()
                .any(|c| c.feature == f.id && c.parameter == parameter)
        };
        claims("bore.diameter")
            && claims("profile_across_flats.length")
            && role_ink(
                &m.text,
                major,
                across,
                f.parameters.get("through").and_then(Value::as_bool) == Some(true),
            )
    })
}

/// Read the diameter at the head and the across-flats value immediately before its unit.
/// Numerical coincidence elsewhere in a callout cannot certify either role (issue 1370).
fn role_ink(text: &str, major: f64, across: f64, through: bool) -> bool {
    let Some((head, profile)) = text.split_once("DOUBLE-D") else {
        return false;
    };
    let Some((_, diameter)) = head.split_once(['⌀', 'ø', 'Ø']) else {
        return false;
    };
    let diameter = diameter.split_whitespace().next().unwrap_or("");
    let mut fields = profile.split_whitespace();
    let width = fields.next().unwrap_or("");
    let unit = fields.next().unwrap_or("");
    diameter.parse::<f64>().is_ok()
        && width.parse::<f64>().is_ok()
        && holes::states(diameter, major)
        && holes::states(width, across)
        && unit == "A/F"
        && (head.split_whitespace().any(|w| w == "THRU") == through)
}

/// The profiled bore findings: one for each key whose bores the callouts do not all document,
/// in Python's order (by key); and one for each bore whose record cannot be read.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut groups: BTreeMap<Key, Vec<(&Feature, f64, f64)>> = BTreeMap::new();
    let mut issues = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "double_d_bore")
    {
        match key(analysis, f) {
            Ok((k, major, across)) => groups.entry(k).or_default().push((f, major, across)),
            Err(why) => issues.push(issue(
                format!("l:profiled_bore_not_dimensioned:{}", f.id),
                format!(
                    "double-D bore {}: its requirements cannot be checked: {why}",
                    f.id
                ),
                vec![f.id.clone()],
                Some(why),
            )),
        }
    }
    for (k, bores) in &groups {
        let need = bores.len();
        let have = bores
            .iter()
            .filter(|(f, major, across)| documented(f, *major, *across, marks))
            .count();
        if have >= need {
            continue;
        }
        let (major, across) = (bores[0].1, bores[0].2);
        let direction: Vec<String> = k
            .direction
            .iter()
            .map(|&c| format!("{:?}", c as f64 / 1e6))
            .collect();
        issues.push(issue(
            format!("l:profiled_bore_not_dimensioned:{}", bores[0].0.id),
            format!(
                "{need} through double-D bore(s) require {} major diameter and {} A/F about {} \
                 with flat direction ({}), but placed callouts document {have}",
                short(major),
                short(across),
                AXES[k.axis].to_uppercase(),
                direction.join(", ")
            ),
            bores.iter().map(|(f, _, _)| f.id.clone()).collect(),
            None,
        ));
    }
    issues
}

fn issue(
    id: String,
    message: String,
    subjects: Vec<String>,
    evidence: Option<String>,
) -> LintIssue {
    LintIssue {
        sheet: None,
        id,
        code: "profiled_bore_not_dimensioned".into(),
        severity: Severity::Warning,
        message,
        at: None,
        view: None,
        subjects,
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence,
        fix: Some(
            "Call out each double-D bore with its major diameter and its size across flats in one \
             callout (`⌀D THRU DOUBLE-D F A/F` for a through bore; state the depth for a blind bore), in the view along its axis."
                .into(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn bore(location: [f64; 3]) -> Value {
        json!({ "axis": [0.0, 0.0, 1.0], "location": location, "major_diameter": 10.0,
                "across_flats": 7.2, "depth": 10.0, "through": true,
                "flat_direction": [-1.0, 0.0, 0.0] })
    }

    fn mark(text: &str, feature: &str, parameters: &[&str]) -> Mark {
        Mark {
            name: format!("hc:{feature}"),
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

    /// test_issue_1370's `double-d-single-z` bore (ø10, 7.2 A/F, flats facing x, through along z):
    /// draftwright's compound callout `⌀10 THRU DOUBLE-D 7.2 A/F` documents it; a diameter-only
    /// callout does not, nor does nothing (draftwright, origin/main 586628b, run on the fixture
    /// with its callout removed, prints exactly this message); two such bores with one
    /// documented are reported as 2 needing, 1 documented.
    #[test]
    fn a_double_d_bore_is_called_out_with_its_diameter_and_across_flats() {
        let both = ["bore.diameter", "profile_across_flats.length"];
        let id = "f:double_d_bore:1";
        let a = part("double_d_bore", bore([0.0, 0.0, 5.0]));
        assert!(coverage(&a, &[mark("⌀10 THRU DOUBLE-D 7.2 A/F", id, &both)]).is_empty());
        let found = coverage(&a, &[mark("⌀10 THRU", id, &both[..1])]);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "profiled_bore_not_dimensioned");
        assert_eq!(
            found[0].message,
            "1 through double-D bore(s) require 10 major diameter and 7.2 A/F about Z with flat \
             direction (1.0, 0.0, 0.0), but placed callouts document 0"
        );
        let mut two = a.clone();
        let mut second = two.features[0].clone();
        second.id = "f:double_d_bore:2".into();
        second.faces = vec![1];
        second.parameters = bore([20.0, 0.0, 5.0]).as_object().unwrap().clone();
        two.features.push(second);
        let found = coverage(&two, &[mark("⌀10 THRU DOUBLE-D 7.2 A/F", id, &both)]);
        assert_eq!(found.len(), 1);
        assert!(found[0].message.starts_with("2 through double-D bore(s)"));
        assert!(found[0].message.ends_with("document 1"));
        assert_eq!(found[0].subjects, [id, "f:double_d_bore:2"]);
    }
}
