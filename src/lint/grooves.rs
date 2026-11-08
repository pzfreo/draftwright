//! Groove coverage (Python's `linting/groove_coverage.py`): every turned groove states its axial
//! width and its floor diameter (LINT-groove_coverage-width_and_floor,
//! LINT-code-groove_requirement_state).
//!
//! Each of the Analysis's `groove` features (quiddity's `Groove`: one annular recess, its `axis`,
//! `at`, `width` and floor `diameter`) has two requirements, `groove.length` (the width) and
//! `groove.diameter`, each judged by [`parameters::coverage`]: an annotation that claims it must
//! print the value (`10 WIDE × ø122` states both). Python joins quiddity's record to its own IR
//! feature by exact geometry before following the measurements; here the Analysis feature is the
//! record, so there is no join to fail. A groove whose record lacks a size has that requirement
//! reported unverifiable.
//!
//! An annular face groove (`annular_groove`, #233) states its outer and inner diameters and its
//! depth (`annular_groove_outer.diameter`, `_inner.diameter`, `annular_groove_depth.length`).
//! Where the hole on its outer wall and the boss on its inner wall are recognised at its
//! diameters ([`annular_owners`]), their diameter and depth dimensions state it in its place: a
//! mark claiming one is read as claiming the groove's requirement, and its text must still print
//! the groove's value.

use serde_json::Value;

use super::parameters::{self, Owners, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::frame;
use crate::model::Analysis;

/// The grooves' requirements: the width, then the floor diameter, of each.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "groove") {
        let width = parameters::size(f, "width");
        out.push(Wanted {
            feature: f,
            parameter: "groove.length".into(),
            callout: width
                .as_ref()
                .map_or("its width".into(), |w| format!("{} WIDE", fmt(*w))),
            values: width.map(|w| vec![w]),
        });
        let diameter = parameters::size(f, "diameter");
        out.push(Wanted {
            feature: f,
            parameter: "groove.diameter".into(),
            callout: diameter
                .as_ref()
                .map_or("its floor diameter".into(), |d| format!("ø{}", fmt(*d))),
            values: diameter.map(|d| vec![d]),
        });
    }
    out
}

/// The groove coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("groove {}", f.id);
        if let (Ok(w), Ok(d)) = (
            parameters::size(f, "width"),
            parameters::size(f, "diameter"),
        ) {
            s += &format!(" {} wide, ø{}", fmt(w), fmt(d));
        }
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            s += &format!(", axis {}", fmt3(a));
        }
        if let Some(at) = parameters::place(&frame, p.get("at")) {
            s += &format!(", at {}", fmt3(at));
        }
        s
    };
    parameters::coverage(
        "groove",
        &wanted(analysis),
        marks,
        noun,
        "a groove callout (width and floor diameter), in a view that shows the turned profile",
    )
}

/// The hole on annular groove *f*'s outer wall and the boss on its inner wall, when both are
/// recognised at its diameters: the groove requirements their dimensions state.
pub(crate) fn annular_owners(analysis: &Analysis, f: &Feature) -> Option<Owners> {
    let wall = |kind: &str, radius: &str| {
        let d = 2.0 * f.parameters.get(radius)?.as_f64()?;
        analysis
            .features
            .iter()
            .find(|w| {
                w.kind == kind
                    && !w.faces.is_empty()
                    && w.faces.iter().all(|i| f.faces.contains(i))
                    && w.parameters
                        .get("diameter")
                        .and_then(Value::as_f64)
                        .is_some_and(|x| (x - d).abs() <= 1e-3)
            })
            .map(|w| w.id.clone())
    };
    let (hole, boss) = (wall("hole", "outer_radius")?, wall("boss", "inner_radius")?);
    Some(vec![
        (
            "annular_groove_outer.diameter",
            hole.clone(),
            "bore.diameter",
        ),
        ("annular_groove_depth.length", hole, "bore.depth"),
        ("annular_groove_inner.diameter", boss, "boss.diameter"),
    ])
}

/// The annular grooves' requirements: the outer and inner diameters, then the depth, of each.
pub fn annular_wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| matches!(f.kind.as_str(), "annular_groove" | "face_groove"))
    {
        let radius = |name| {
            if f.kind == "face_groove" {
                parameters::size(
                    f,
                    if name == "outer_radius" {
                        "outer_diameter"
                    } else {
                        "inner_diameter"
                    },
                )
            } else {
                parameters::size(f, name).map(|r| 2.0 * r)
            }
        };
        for (parameter, value, word) in [
            ("annular_groove_outer.diameter", radius("outer_radius"), "ø"),
            ("annular_groove_inner.diameter", radius("inner_radius"), "ø"),
            (
                "annular_groove_depth.length",
                parameters::size(f, "depth"),
                "↧",
            ),
        ] {
            out.push(Wanted {
                feature: f,
                parameter: parameter.into(),
                callout: value
                    .as_ref()
                    .map_or(format!("its {parameter}"), |v| format!("{word}{}", fmt(*v))),
                values: value.map(|v| vec![v]),
            });
        }
    }
    for f in analysis.features.iter().filter(|f| f.kind == "face_groove") {
        for name in ["outer_wall_angle", "inner_wall_angle"] {
            let angle = parameters::size(f, name);
            if angle.as_ref().is_ok_and(|a| (*a - 90.0).abs() <= 1e-3) {
                continue;
            }
            out.push(Wanted {
                feature: f,
                parameter: format!("face_groove_{name}.angle"),
                callout: angle
                    .as_ref()
                    .map_or("its wall angle".into(), |a| format!("{}°", fmt(*a))),
                values: angle.map(|a| vec![a]),
            });
        }
    }
    out
}

/// The annular groove coverage findings.
pub fn annular_coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut all = marks.to_vec();
    for f in analysis
        .features
        .iter()
        .filter(|f| matches!(f.kind.as_str(), "annular_groove" | "face_groove"))
    {
        if let Some(owners) = annular_owners(analysis, f) {
            all.extend(parameters::relay(marks, f, &owners));
        }
    }
    let noun = |f: &Feature| {
        let mut s = format!("annular groove {}", f.id);
        if let (Ok(o), Ok(i), Ok(d)) = (
            parameters::size(f, "outer_radius"),
            parameters::size(f, "inner_radius"),
            parameters::size(f, "depth"),
        ) {
            s += &format!(" ø{} × ø{} × {} deep", fmt(2.0 * o), fmt(2.0 * i), fmt(d));
        }
        s
    };
    parameters::coverage(
        "annular_groove",
        &annular_wanted(analysis),
        &all,
        noun,
        "the groove's callout (outer and inner diameters and depth), in the view down its axis",
    )
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn mark(text: &str, parameters: &[&str]) -> Mark {
        Mark {
            name: "m_groove_z0".into(),
            view: Some("v:front".into()),
            text: text.into(),
            claims: parameters
                .iter()
                .map(|p| Claim {
                    feature: "f:groove:1".into(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    /// connector109's first groove (quiddity 0.3.9): `10 WIDE × ø122` states both requirements;
    /// a callout of the width alone leaves the diameter missing; a misprinted floor diameter is
    /// unverifiable; a record without its width cannot be checked.
    #[test]
    fn a_groove_states_its_width_and_floor_diameter() {
        let record = json!({ "at": [-116.39, 0.024, 0.003], "axis": "x", "diameter": 122.0,
                             "width": 10.0 });
        let a = part("groove", record.clone());
        let both = ["groove.length", "groove.diameter"];
        assert!(coverage(&a, &[mark("10 WIDE × ø122", &both)]).is_empty());
        let codes = |a: &Analysis, marks: &[Mark]| -> Vec<String> {
            coverage(a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        assert_eq!(
            codes(&a, &[mark("10 WIDE", &["groove.length"])]),
            ["groove_requirement_missing groove.diameter"]
        );
        assert_eq!(
            codes(&a, &[mark("10 WIDE × ø120", &both)]),
            ["groove_requirement_unverifiable groove.diameter"]
        );
        let mut bad = record;
        bad.as_object_mut().unwrap().remove("width");
        assert_eq!(
            codes(&part("groove", bad), &[mark("10 WIDE × ø122", &both)]),
            ["groove_requirement_unverifiable groove.length"]
        );
        let issue = &coverage(&a, &[])[0];
        assert_eq!(
            issue.message,
            "groove f:groove:1 10 wide, ø122, axis (1, 0, 0), at (-116.39, 0.024, 0.003): \
             requirement groove.length (10 WIDE) is not stated on the sheet"
        );
    }

    /// #233: an annular groove's diameters and depth are stated by its own callout, or by the
    /// hole and boss on its walls, whose printed values must be the groove's.
    #[test]
    fn an_annular_groove_states_its_diameters_and_depth() {
        let mut a = part(
            "annular_groove",
            json!({ "axis": [0.0, 0.0, 1.0], "floor_center": [30.0, 20.0, 8.0],
                    "inner_radius": 1.0, "outer_radius": 1.5, "depth": 2.0 }),
        );
        a.features[0].faces = vec![3, 4, 5];
        let claim = |feature: &str, parameter: &str| Claim {
            feature: feature.into(),
            parameter: parameter.into(),
        };
        let own = Mark {
            name: "m_groove".into(),
            view: Some("v:plan".into()),
            text: "⌀3 × ⌀2 ↧ 2".into(),
            claims: [
                "annular_groove_outer.diameter",
                "annular_groove_inner.diameter",
                "annular_groove_depth.length",
            ]
            .map(|p| claim("f:annular_groove:1", p))
            .into(),
        };
        assert!(annular_coverage(&a, &[own]).is_empty());
        assert_eq!(annular_coverage(&a, &[]).len(), 3);

        let mut wall = |kind: &str, face: usize, diameter: f64| {
            let mut w = a.features[0].clone();
            w.id = format!("f:{kind}:1");
            w.kind = kind.into();
            w.faces = vec![face];
            w.parameters = json!({ "diameter": diameter }).as_object().unwrap().clone();
            a.features.push(w);
        };
        wall("hole", 3, 3.0);
        wall("boss", 5, 2.0);
        let hole = Mark {
            name: "m_hole".into(),
            view: Some("v:plan".into()),
            text: "⌀3 ↧ 2".into(),
            claims: vec![
                claim("f:hole:1", "bore.diameter"),
                claim("f:hole:1", "bore.depth"),
            ],
        };
        let boss = |text: &str| Mark {
            name: "m_boss".into(),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![claim("f:boss:1", "boss.diameter")],
        };
        assert!(annular_coverage(&a, &[hole.clone(), boss("ø2")]).is_empty());
        let codes: Vec<String> = annular_coverage(&a, &[hole, boss("ø2.5")])
            .into_iter()
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect();
        assert_eq!(
            codes,
            ["annular_groove_requirement_unverifiable annular_groove_inner.diameter"]
        );
    }
}
