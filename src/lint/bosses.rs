//! Boss height coverage (Python's `lint_boss_height_coverage` in `linting/coverage.py`): every
//! boss with a modelled height has that height stated by a linear dimension; a diameter callout
//! does not cover it (LINT-code-boss_height_missing).
//!
//! Each of the Analysis's `boss` features (quiddity's `BossRecord`: `diameter`, `height`, `axis`,
//! `location`) whose record gives a height has one requirement, `boss_height.length`, judged by
//! [`parameters::coverage`]: an annotation that claims it must print the height. A boss without a
//! height is outside the check, as in Python (it keeps the diameter-only contract).
//!
//! A boss that is a groove's floor is the groove's, not a boss (draftwright's `boss_groove_owner`
//! in `model/detect.py`): quiddity reports the floor of a turned groove as a boss too, and the
//! groove's own callout states its width and floor diameter. Here that is a boss sharing a face
//! with a `groove` feature of the same diameter (within 0.15 mm, draftwright's `_DIA_TOL`).
//!
//! Python reports one finding for the whole sheet (`<n> boss height(s) are not dimensioned`); the
//! port reports one per boss, under the same code, naming it, so a caller can act on each. A
//! height that is claimed but misprinted is not stated either, so it is reported under the same
//! code, with the annotation and why. Not ported: a height consolidated onto an overall extent
//! (Python follows the authored omission's `conveyed_by`, which lint cannot see without the
//! plan). A blend's same-face radius owns only the boss diameter ([`boss_owner`]); its height
//! remains independently required. The diameter backstop uses that same ownership evidence.
//!
//! A boss that is a band of a turned profile is the turned step's, not a boss (draftwright's
//! `boss_turned_step_owner` in `model/detect.py`'s `_append_turned_and_boss_features`): quiddity
//! reports each band of a turned part both as a turned step and as a boss, and draftwright emits
//! no boss feature for one whose record names the same defining faces as a turned step's
//! (`_records_share_defining_target`), so the step's diameter and length state it. Here that is a
//! boss whose faces are a `turned_step` feature's, exactly ([`turned_band`]).

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{frame, point, principal};
use crate::model::Analysis;

/// Whether boss *b* is the floor of one of the Analysis's grooves: it shares a face with a groove
/// of its diameter (within 0.15 mm).
pub fn groove_floor(analysis: &Analysis, b: &Feature) -> bool {
    let Ok(d) = parameters::size(b, "diameter") else {
        return false;
    };
    analysis
        .features
        .iter()
        .filter(|g| g.kind == "groove")
        .any(|g| {
            parameters::size(g, "diameter").is_ok_and(|gd| (gd - d).abs() <= 0.15)
                && g.faces.iter().any(|i| b.faces.binary_search(i).is_ok())
        })
}

/// Whether boss *b* is a band of a turned profile: a `turned_step` feature has exactly its faces
/// (draftwright's same-run evidence test, which an empty face set never passes).
pub fn turned_band(analysis: &Analysis, b: &Feature) -> bool {
    !b.faces.is_empty()
        && analysis
            .features
            .iter()
            .any(|s| s.kind == "turned_step" && s.faces == b.faces)
}

/// A blend owns a boss's diameter only with one same-run, nonempty face match and the same
/// circle and axis. Its independent height remains required (Python's boss_blend_owner_pairs).
pub fn boss_owner<'a>(a: &'a Analysis, boss: &Feature) -> Option<&'a Feature> {
    if boss.faces.is_empty() || boss.kind != "boss" {
        return None;
    }
    let p = &boss.parameters;
    let axis = principal(point(p.get("axis"))?)?;
    let diameter = p.get("diameter")?.as_f64()?;
    let candidates: Vec<_> = a
        .features
        .iter()
        .filter(|b| {
            b.kind == "blend"
                && b.faces == boss.faces
                && b.parameters
                    .get("radius")
                    .and_then(|r| r.as_f64())
                    .is_some_and(|r| r > 0.0 && (diameter - 2.0 * r).abs() <= 0.15)
                && b.parameters
                    .get("path")
                    .and_then(|p| point(p.get("direction").or(p.get("normal"))))
                    .and_then(principal)
                    == Some(axis)
        })
        .collect();
    let [blend] = candidates.as_slice() else {
        return None;
    };
    (a.features
        .iter()
        .filter(|b| b.kind == "boss" && b.faces == boss.faces)
        .count()
        == 1)
        .then_some(*blend)
}

/// The bosses' height requirements.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "boss" && !groove_floor(analysis, f) && !turned_band(analysis, f))
        .filter_map(|f| {
            let h = parameters::size(f, "height").ok()?;
            Some(Wanted {
                feature: f,
                parameter: "boss_height.length".into(),
                values: Ok(vec![h]),
                callout: format!("its height {}", fmt(h)),
            })
        })
        .collect()
}

/// The boss height findings, each `boss_height_missing`.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("boss {}", f.id);
        if let Ok(d) = parameters::size(f, "diameter") {
            s += &format!(" ø{}", fmt(d));
        }
        if let Ok(h) = parameters::size(f, "height") {
            s += &format!(", height {}", fmt(h));
        }
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            s += &format!(", axis {}", fmt3(a));
        }
        if let Some(at) = parameters::place(&frame, p.get("location")) {
            s += &format!(", at {}", fmt3(at));
        }
        s
    };
    let mut issues = parameters::coverage(
        "boss_height",
        &wanted(analysis),
        marks,
        noun,
        "a linear dimension along the boss's axis, in a view that sees it side-on",
    );
    // Independently prove a flush hub from its physical interval and stated envelope.
    issues.retain(|i| {
        let Some(id) = i.subjects.first() else {
            return true;
        };
        let Some(s) = super::redundancy::span(analysis, id, "boss_height.length") else {
            return true;
        };
        let whole = super::redundancy::Span::new(
            s.axis,
            analysis.envelope[0][s.axis],
            analysis.envelope[1][s.axis],
        );
        !(s.same(whole)
            && marks.iter().any(|m| {
                m.claims.iter().any(|c| {
                    c.feature == "envelope" && c.parameter == super::envelope::PARAMETERS[s.axis]
                }) && super::holes::states_in(&m.text, whole.hi - whole.lo, m.units)
            }))
    });
    for i in &mut issues {
        i.code = "boss_height_missing".into();
        i.id = format!("l:boss_height_missing:{}/boss_height.length", i.subjects[0]);
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::{Value, json};

    fn part(features: &[(&str, &[usize], Value)]) -> Analysis {
        let features: Vec<Value> = features
            .iter()
            .enumerate()
            .map(|(i, (kind, faces, r))| {
                json!({
                    "id": format!("f:{kind}:{}", i + 1), "kind": kind, "faces": faces,
                    "parameters": r,
                    "fingerprint": { "version": "test", "family": "bosses",
                                     "subtype": "BossRecord", "sizes": {},
                                     "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
                })
            })
            .collect();
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 9, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [100.0, 100.0, 10.0]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        }))
        .unwrap()
    }

    fn boss(diameter: f64, height: Option<f64>) -> Value {
        json!({ "axis": [0.0, 1.0, 0.0], "location": [6.3, 7.6, -1.0], "diameter": diameter,
                "height": height })
    }

    /// LINT-code-boss_height_missing: a boss's height is stated by a dimension that claims it and
    /// prints it (m10127's boss, ø8.4, height 5.73, stated `5.7`); a diameter callout does not
    /// state it; a boss without a modelled height is not checked; a groove's floor is the
    /// groove's (connector109's ø122 band under its groove).
    #[test]
    fn a_boss_states_its_height_by_a_dimension() {
        let a = part(&[
            ("boss", &[4, 6], boss(8.38537, Some(5.73))),
            ("boss", &[7], boss(10.0, None)),
            ("boss", &[1], boss(122.0, Some(6.0))),
            (
                "groove",
                &[1, 2],
                json!({ "diameter": 122.0, "width": 24.04 }),
            ),
        ]);
        let mark = |text: &str, p: &str| Mark {
            units: Default::default(),
            name: format!("m:{p}"),
            view: Some("v:side".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: "f:boss:1".into(),
                parameter: p.into(),
            }],
        };
        assert!(coverage(&a, &[mark("5.7", "boss_height.length")]).is_empty());
        let found = coverage(&a, &[mark("ø8.4", "boss.diameter")]);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "boss_height_missing");
        assert_eq!(
            found[0].id,
            "l:boss_height_missing:f:boss:1/boss_height.length"
        );
        assert_eq!(
            found[0].message,
            "boss f:boss:1 ø8.385, height 5.73, axis (0, 1, 0), at (6.3, 7.6, -1): requirement boss_height.length (its height 5.73) is not stated on the sheet"
        );
        let misprinted = coverage(&a, &[mark("6.7", "boss_height.length")]);
        assert_eq!(misprinted[0].code, "boss_height_missing");
        assert_eq!(
            misprinted[0].evidence.as_deref(),
            Some("\"6.7\" does not state its height 5.73")
        );
    }
}
