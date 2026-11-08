//! Blend coverage (Python's `linting/blend_coverage.py`): every recognised blend chain states its
//! radius (LINT-blend_coverage-radius, LINT-code-blend_requirement_state).
//!
//! Each of the Analysis's `blend` features (quiddity's `Blend`: a `radius`, a `side`, and a
//! `path`, straight (`at`, `direction`) or circular (`center`, `normal`, `radius`)) has one
//! requirement, `blend.radius`, judged by [`parameters::coverage`]. A blend whose path is neither
//! is still a radius to state; only its description in messages is shorter.
//!
//! Not ported: Python's check that a placed radius leader's tip touches the blend's trimmed arc
//! (`lint_blend_leader_targets`), which needs the drawn geometry.

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{frame, principal};
use crate::model::Analysis;

/// The blends' requirements.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "blend" && !super::blends::is_relief(analysis, f))
        .map(|f| {
            let radius = parameters::size(f, "radius");
            Wanted {
                feature: f,
                parameter: "blend.radius".into(),
                callout: radius
                    .as_ref()
                    .map_or("R?".into(), |r| format!("R{}", fmt(*r))),
                values: radius.map(|r| vec![r]),
            }
        })
        .collect()
}

/// The blend coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("blend {}", f.id);
        if let Ok(r) = parameters::size(f, "radius") {
            s += &format!(" R{}", fmt(r));
        }
        if let Some(side) = p.get("side").and_then(|v| v.as_str()) {
            s += &format!(" {side}");
        }
        let path = p.get("path");
        let get = |k: &str| path.and_then(|v| v.get(k));
        if let (Some(at), Some(d)) = (
            parameters::place(&frame, get("at")),
            parameters::direction(&frame, get("direction")),
        ) {
            s += &format!(", straight along {} through {}", fmt3(d), fmt3(at));
        } else if let (Some(c), Some(n)) = (
            parameters::place(&frame, get("center")),
            parameters::direction(&frame, get("normal")),
        ) {
            s += &format!(", circular about {} centred at {}", fmt3(n), fmt3(c));
            if let Some(r) = get("radius").and_then(|v| v.as_f64()) {
                s += &format!(" (path radius {})", fmt(r));
            }
        }
        s
    };
    parameters::coverage(
        "blend",
        &wanted(analysis),
        marks,
        noun,
        "a radius callout, in a view that sees the blend's arc",
    )
}

pub(crate) const RELIEF_NOTE: &str = "n:f:blend:relief/profiles";
pub(crate) const RADIUS_TABLE: &str = "t:radius-centres";

fn radius_view(axis: usize) -> &'static str {
    ["side", "front", "plan"][axis]
}

pub(crate) fn is_relief(a: &Analysis, f: &Feature) -> bool {
    matches!(f.kind.as_str(), "blend" | "fillet")
        && !f.faces.is_empty()
        && a.radius_reliefs
            .iter()
            .any(|r| f.faces.iter().all(|face| r.faces.contains(face)))
}

/// Geometric relief exclusion needs an explicit, rendered profile requirement. This grants
/// no radius claims and cannot be supplied by an unrelated authored prose note.
pub(crate) fn relief_stated(a: &Analysis, drawing: &crate::output::Drawing) -> bool {
    a.radius_reliefs.is_empty()
        || drawing.annotations.iter().any(|n| {
            n.name == RELIEF_NOTE
                && n.claims.is_empty()
                && n.satisfies.is_empty()
                && crate::output::drawn_body(drawing, n)
                    .is_some_and(|text| text.ends_with("SHALLOW RELIEF PROFILES PER MODEL"))
        })
}

/// A stale/wrong centre row cannot state a radius or unlock the blanket default. Lint
/// checks printed locator cells as well as the radius and the exact occurrence identity.
pub(crate) fn verified_row(a: &Analysis, m: &Mark) -> bool {
    let Some(id) = m.name.strip_prefix(&format!("{RADIUS_TABLE}/")) else {
        return true;
    };
    let Some(f) = a.features.iter().find(|f| f.id == id && f.kind == "blend") else {
        return false;
    };
    if m.claims.len() != 1 || m.claims[0].feature != f.id || m.claims[0].parameter != "blend.radius"
    {
        return false;
    }
    let Some((view, centre)) = table_centre(a, f) else {
        return false;
    };
    if m.view.as_deref() != Some(format!("v:{view}").as_str()) {
        return false;
    }
    let Ok(r) = parameters::size(f, "radius") else {
        return false;
    };
    let cells: Vec<_> = m.text.split_whitespace().collect();
    cells.len() == 4
        && cells[0] == view.to_uppercase()
        && cells[1] == fmt(centre[0])
        && cells[2] == fmt(centre[1])
        && cells[3].strip_prefix('R').is_some_and(|value| {
            value.as_bytes().first().is_some_and(u8::is_ascii_digit)
                && value.bytes().all(|b| b.is_ascii_digit() || b == b'.')
                && value.parse::<f64>().is_ok_and(|v| v.is_finite() && v > 0.0)
                && super::holes::states(value, r)
        })
}

/// The recognition path is a radius centre only when the blend's own trimmed surface
/// samples agree with its circle in this end view. A torus path or stale centre fails closed.
pub(crate) fn table_centre(a: &Analysis, f: &Feature) -> Option<(&'static str, [f64; 2])> {
    let path = f.parameters.get("path")?;
    let axis = parameters::direction(&frame(a), path.get("direction")).and_then(principal)?;
    let at = parameters::place(&frame(a), path.get("at"))?;
    let (x, y) = match axis {
        0 => (1, 2),
        1 => (0, 2),
        _ => (0, 1),
    };
    let r = parameters::size(f, "radius").ok()?;
    let sites = a.blend_sites.get(&f.id)?;
    if sites.is_empty()
        || sites
            .iter()
            .any(|p| ((p[x] - at[x]).hypot(p[y] - at[y]) - r).abs() > 0.005)
    {
        return None;
    }
    Some((
        radius_view(axis),
        [at[x] - a.envelope[0][x], at[y] - a.envelope[0][y]],
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::{Value, json};

    fn part(records: &[Value]) -> Analysis {
        let features: Vec<Value> = records
            .iter()
            .enumerate()
            .map(|(i, r)| {
                json!({
                    "id": format!("f:blend:{}", i + 1), "kind": "blend", "faces": [i],
                    "parameters": r,
                    "fingerprint": { "version": "test", "family": "blends", "subtype": "Blend",
                                     "sizes": {}, "position": [0.0, 0.0, 0.0],
                                     "neighbourhood": [] },
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

    /// LINT-blend_coverage-radius: a straight or a circular-path blend states its radius
    /// (string_post's `2× R0.2` states its two R0.25 blends at one decimal); a blend whose record
    /// gives no radius is unverifiable.
    #[test]
    fn a_blend_states_its_radius_whatever_its_path() {
        let a = part(&[
            json!({ "radius": 0.25, "side": "convex",
                    "path": { "center": [-4.546, 0.0, 0.0], "normal": [1.0, 0.0, 0.0],
                              "radius": 3.5 } }),
            json!({ "radius": 2.0, "side": "concave",
                    "path": { "at": [-15.739, 83.503, -119.75], "direction": [0.0, 0.0, 1.0] } }),
            json!({ "side": "concave",
                    "path": { "at": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0] } }),
        ]);
        let mark = |text: &str, f: &str| Mark {
            name: format!("m:{f}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: f.into(),
                parameter: "blend.radius".into(),
            }],
        };
        let issues = coverage(&a, &[mark("2× R0.2", "f:blend:1")]);
        let found: Vec<String> = issues
            .iter()
            .map(|i| format!("{} {}", i.code, i.subjects[0]))
            .collect();
        assert_eq!(
            found,
            [
                "blend_requirement_missing f:blend:2",
                "blend_requirement_unverifiable f:blend:3"
            ]
        );
        assert_eq!(
            issues[0].message,
            "blend f:blend:2 R2 concave, straight along (0, 0, 1) through (-15.739, 83.503, -119.75): requirement blend.radius (R2) is not stated on the sheet"
        );
        // Misprinted: claimed, but R0.4 is not R0.25 at one decimal.
        assert_eq!(
            coverage(&a, &[mark("R0.4", "f:blend:1")])[0].code,
            "blend_requirement_unverifiable"
        );
    }

    #[test]
    fn radius_rows_keep_precise_centres_and_validate_the_displayed_radius() {
        let mut a = part(&[json!({"radius":0.25,
            "path":{"at":[12.3456,67.8901,0.0],"direction":[0.0,0.0,1.0]}})]);
        a.blend_sites
            .insert("f:blend:1".into(), vec![[12.5956, 67.8901, 0.0]]);
        let mut row = Mark {
            name: format!("{RADIUS_TABLE}/f:blend:1"),
            view: Some("v:plan".into()),
            text: "PLAN 12.346 67.890 R0.25".into(),
            claims: vec![Claim {
                feature: "f:blend:1".into(),
                parameter: "blend.radius".into(),
            }],
        };
        assert!(verified_row(&a, &row));
        for text in [
            "PLAN 12.3 67.9 R0.25",
            "PLAN 12.346 67.890 R0.4",
            "PLAN 12.346 67.890 R67.89",
            "PLAN 12.346 67.890 R0.25e5",
        ] {
            row.text = text.into();
            assert!(!verified_row(&a, &row), "{text}");
        }
        a.features[0].parameters.insert("radius".into(), json!(2.1));
        a.blend_sites
            .insert("f:blend:1".into(), vec![[14.4456, 67.8901, 0.0]]);
        row.text = "PLAN 12.346 67.890 R.2".into();
        assert!(!verified_row(&a, &row));
    }
}
