//! Flat coverage (Python's `linting/flat_coverage.py`): each machined flat, or each pair of flats
//! of one double-D or hex, states its across-flats size (LINT-flat_coverage-across_flats,
//! LINT-code-flat_requirement_state).
//!
//! quiddity's `Flat` gives each flat face's stock: its `axis` letter and `axis_direction`, its
//! `axis_line` (the stock's centre across the axis), its `stock_span` along it, and the
//! `across` size. Flats with the same axis, direction (its sign made positive along the named
//! axis, to 1e-6), axis line, span and across size (each to 1e-3, as Python rounds them) are one
//! physical requirement, `flat.length` ([`groups`]): the two faces of a double-D share one, and
//! parallel, differently directed or disjoint coaxial stock does not. Each group is judged by
//! [`parameters::coverage`]: an annotation that claims `flat.length` of any of its flats must
//! print the size (`2.4 A/F`). The finding names the group's first flat as its requirement and
//! every flat as its subjects. A flat whose record does not give its stock is a group of its own,
//! reported unverifiable.

use serde_json::Value;

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::frame;
use crate::model::Analysis;

fn qualifier(f: &Feature) -> &'static str {
    if f.parameters.get("opposed").and_then(Value::as_bool) == Some(false) {
        ""
    } else {
        " A/F"
    }
}

/// One physical across-flats requirement: the flats that share it, in Analysis order.
#[derive(Clone, Debug, PartialEq)]
pub struct FlatGroup<'a> {
    pub members: Vec<&'a Feature>,
}

/// A record's number pair *name*, to 1e-3.
fn pair(p: &serde_json::Map<String, Value>, name: &str) -> Option<[i64; 2]> {
    let a = p.get(name)?.as_array()?;
    let r = |v: &Value| v.as_f64().map(|x| (x * 1e3).round() as i64);
    match a.as_slice() {
        [x, y] => Some([r(x)?, r(y)?]),
        _ => None,
    }
}

/// A flat's requirement identity (Python's `_FlatRequirementKey`): its axis letter, direction (to
/// 1e-6), axis line, stock span and across size (to 1e-3).
type Key = (String, [i64; 3], [i64; 2], [i64; 2], i64);

/// The identity of a flat's requirement, or none when its record does not give it.
fn key(f: &Feature) -> Option<Key> {
    let p = &f.parameters;
    let axis = p.get("axis")?.as_str()?;
    let k = ["x", "y", "z"].iter().position(|a| *a == axis)?;
    let mut d = [0.0; 3];
    d[k] = 1.0;
    if let Some(v) = p.get("axis_direction") {
        d = crate::bridge::holes::point(Some(v))?;
    }
    let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
    if n == 0.0 {
        return None;
    }
    let sign = if d[k] < 0.0 { -1.0 } else { 1.0 };
    let direction = d.map(|c| (sign * c / n * 1e6).round() as i64);
    let across = (p.get("across")?.as_f64()? * 1e3).round() as i64;
    Some((
        axis.to_string(),
        direction,
        pair(p, "axis_line")?,
        pair(p, "stock_span")?,
        across,
    ))
}

/// The flats grouped by the requirement they share, in the order of each group's first flat.
pub fn groups(analysis: &Analysis) -> Vec<FlatGroup<'_>> {
    let mut keyed: Vec<(Option<_>, FlatGroup)> = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "flat") {
        let k = key(f);
        match keyed.iter_mut().find(|(g, _)| k.is_some() && *g == k) {
            Some((_, g)) => g.members.push(f),
            None => keyed.push((k, FlatGroup { members: vec![f] })),
        }
    }
    keyed.into_iter().map(|(_, g)| g).collect()
}

/// The flats' requirements: one `flat.length` per group, on its first flat.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    groups(analysis)
        .into_iter()
        .map(|g| {
            let f = g.members[0];
            let values = if key(f).is_none() {
                Err("its record does not give its stock (axis, line, span and across size)".into())
            } else {
                parameters::size(f, "across").map(|a| vec![a])
            };
            Wanted {
                feature: f,
                parameter: "flat.length".into(),
                callout: values.as_ref().map_or("its across-flats size".into(), |v| {
                    format!("{}{}", fmt(v[0]), qualifier(f))
                }),
                values,
            }
        })
        .collect()
}

/// The flat coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let groups = groups(analysis);
    // A claim of any flat of a group is a claim of the group's requirement, which
    // `parameters::coverage` keys by the group's first flat.
    let first = |id: &str| {
        groups
            .iter()
            .find(|g| g.members.iter().any(|m| m.id == id))
            .map(|g| g.members[0].id.clone())
    };
    let marks: Vec<Mark> = marks
        .iter()
        .cloned()
        .map(|mut m| {
            for c in &mut m.claims {
                if c.parameter == "flat.length"
                    && let Some(id) = first(&c.feature)
                {
                    c.feature = id;
                }
            }
            m
        })
        .collect();
    let noun = |f: &Feature| {
        // Invariant: Each representative feature f was made from a nonempty group with this first member.
        let g = groups.iter().find(|g| g.members[0].id == f.id).unwrap();
        let ids: Vec<&str> = g.members.iter().map(|m| m.id.as_str()).collect();
        let mut s = format!("flat {}", ids.join(" and "));
        let p = &f.parameters;
        if let Ok(a) = parameters::size(f, "across") {
            s += &format!(" {}{}", fmt(a), qualifier(f));
        }
        if let Some(d) = parameters::direction(&frame, p.get("axis_direction"))
            .or_else(|| parameters::direction(&frame, p.get("axis")))
        {
            s += &format!(", stock along {}", fmt3(d));
        }
        let at: Vec<String> = g
            .members
            .iter()
            .filter_map(|m| parameters::place(&frame, m.parameters.get("at")))
            .map(fmt3)
            .collect();
        if !at.is_empty() {
            s += &format!(", at {}", at.join(" and "));
        }
        s
    };
    let mut wanted = wanted(analysis);
    for w in &mut wanted {
        if w.feature.parameters.get("opposed").and_then(Value::as_bool) == Some(false)
            && marks.iter().any(|m| {
                m.claims
                    .iter()
                    .any(|c| c.feature == w.feature.id && c.parameter == "flat_offset.length")
            })
            && let Some(offset) = w
                .feature
                .parameters
                .get("axis_distance")
                .and_then(Value::as_f64)
        {
            w.parameter = "flat_offset.length".into();
            w.callout = format!("{} from axis", fmt(offset));
            w.values = Ok(vec![offset]);
        }
    }
    let mut issues = parameters::coverage(
        "flat",
        &wanted,
        &marks,
        noun,
        "an across-flats dimension, in a view that sees the flats edge-on",
    );
    for issue in &mut issues {
        if let Some(g) = groups.iter().find(|g| g.members[0].id == issue.subjects[0]) {
            issue.subjects = g.members.iter().map(|m| m.id.clone()).collect();
        }
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn part(records: &[Value]) -> Analysis {
        let features: Vec<Value> = records
            .iter()
            .enumerate()
            .map(|(i, r)| {
                json!({
                    "id": format!("f:flat:{}", i + 1), "kind": "flat", "faces": [i],
                    "parameters": r,
                    "fingerprint": { "version": "test", "family": "flats", "subtype": "Flat",
                                     "sizes": {}, "position": [0.0, 0.0, 0.0],
                                     "neighbourhood": [] },
                })
            })
            .collect();
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 2, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
            "recognition": { "recogniser": "test",
                             "frame": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]] },
        }))
        .unwrap()
    }

    fn mark(text: &str, features: &[&str]) -> Mark {
        Mark {
            name: "m_flat_z0".into(),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: features
                .iter()
                .map(|f| Claim {
                    feature: f.to_string(),
                    parameter: "flat.length".into(),
                })
                .collect(),
        }
    }

    /// string_post's double-D (quiddity 0.3.9): its two flat faces share one requirement, which
    /// a `2.4 A/F` claiming either face states; reversed in direction they are still one. A
    /// flat on other stock (another span) is a requirement of its own.
    #[test]
    fn the_two_faces_of_a_double_d_share_one_across_flats_requirement() {
        let face = |y: f64, dir: f64, span: [f64; 2]| {
            json!({ "across": 2.4, "at": [6.404, y, 0.0], "axis": "x",
                    "axis_direction": [dir, 0.0, 0.0], "axis_line": [0.0, 0.0],
                    "stock_span": span })
        };
        let a = part(&[
            face(-1.2, 1.0, [2.804, 10.004]),
            face(1.2, -1.0, [2.804, 10.004]),
        ]);
        assert_eq!(groups(&a).len(), 1);
        assert!(coverage(&a, &[mark("2.4 A/F", &["f:flat:2"])]).is_empty());
        let issues = coverage(&a, &[]);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].code, "flat_requirement_missing");
        assert_eq!(issues[0].subjects, ["f:flat:1", "f:flat:2"]);
        assert_eq!(
            issues[0].message,
            "flat f:flat:1 and f:flat:2 2.4 A/F, stock along (1, 0, 0), at (6.404, -1.2, 0) and \
             (6.404, 1.2, 0): requirement flat.length (2.4 A/F) is not stated on the sheet"
        );
        let misprinted = coverage(&a, &[mark("2.5 A/F", &["f:flat:1"])]);
        assert_eq!(misprinted[0].code, "flat_requirement_unverifiable");

        let b = part(&[face(-1.2, 1.0, [2.804, 10.004]), face(1.2, 1.0, [0.0, 2.0])]);
        let found: Vec<(String, Vec<String>)> = coverage(&b, &[mark("2.4 A/F", &["f:flat:1"])])
            .into_iter()
            .map(|i| (i.code, i.subjects))
            .collect();
        assert_eq!(
            found,
            [(
                "flat_requirement_missing".to_string(),
                vec!["f:flat:2".to_string()]
            )]
        );
        // A record without its stock cannot be grouped or checked.
        let c = part(&[json!({ "across": 2.4, "axis": "x" })]);
        let issues = coverage(&c, &[mark("2.4 A/F", &["f:flat:1"])]);
        assert_eq!(issues[0].code, "flat_requirement_unverifiable");
    }
}
