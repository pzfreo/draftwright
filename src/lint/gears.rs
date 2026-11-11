//! Python linting/gear_coverage.py: authored requirements versus actual table ink and
//! full-wire recognition evidence. A radial arc count alone never proves correspondence.
use serde_json::{Map, Value};

use super::{LintIssue, Severity};
use crate::bridge::holes::{dir_to_part, frame, point, to_part};
use crate::model::Analysis;
use crate::output::{Drawing, Kind};

fn issue(code: &str, key: &str, severity: Severity, message: &str) -> LintIssue {
    LintIssue {
        sheet: None,
        id: format!("l:{code}:{key}"),
        code: code.into(),
        severity,
        message: message.into(),
        at: None,
        view: None,
        subjects: vec![key.into()],
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence: None,
        fix: None,
    }
}

// Independent oracle: do not accept a compiler's row snapshot as evidence of its own truth.
fn number(v: f64) -> String {
    if v == 0.0 {
        return "0".into();
    }
    if v.fract() == 0.0 {
        return format!("{v:.0}");
    }
    if v.abs() < 1e-4 {
        let text = format!("{v:e}");
        // Invariant: The finite small nonzero magnitude branch uses Rust scientific notation.
        let (m, e) = text.split_once('e').unwrap();
        // Invariant: Rust scientific notation emits an integer exponent that fits i32.
        return format!("{m}e{:+03}", e.parse::<i32>().unwrap());
    }
    v.to_string()
}

fn expected(p: &Map<String, Value>, units: crate::output::Units) -> Option<Vec<String>> {
    let unit = units.symbol();
    let length = |v: f64| {
        if units.is_mm() {
            number(v)
        } else {
            crate::output::Units::inch_digits(format!("{:.3}", v / 25.4))
        }
    };
    let len = |key: &str| Some(length(p.get(key)?.as_f64()?));
    let n = |key: &str| Some(number(p.get(key)?.as_f64()?));
    let signed = |v: f64| {
        if v > 0.0 {
            format!(
                "+{}",
                if units.is_mm() {
                    number(v)
                } else {
                    units.magnitude(v)
                }
            )
        } else {
            if units.is_mm() {
                number(v)
            } else {
                units.magnitude(v)
            }
        }
    };
    let tol = p.get("tooth_thickness_tolerance")?.as_array()?;
    let lo = tol.first()?.as_f64()?;
    let hi = tol.get(1)?.as_f64()?;
    let geo = "ISO 21771-1:2024";
    Some(
        [
            ["GEAR DATA".into(), "VALUE".into(), "BASIS".into()],
            ["TYPE".into(), "METRIC EXTERNAL SPUR".into(), geo.into()],
            ["BASIC RACK".into(), "STANDARD".into(), "ISO 53:1998".into()],
            [
                "TEETH z".into(),
                p.get("tooth_count")?.as_i64()?.to_string(),
                geo.into(),
            ],
            [
                "MODULE m".into(),
                format!("{} {unit}", len("module")?),
                "ISO 54:1996".into(),
            ],
            [
                "PRESSURE ANGLE".into(),
                format!("{} deg", n("pressure_angle")?),
                geo.into(),
            ],
            ["PROFILE SHIFT x".into(), n("profile_shift")?, geo.into()],
            [
                "FACE WIDTH b".into(),
                format!("{} {unit}", len("face_width")?),
                geo.into(),
            ],
            [
                "TOOTH THICKNESS s".into(),
                format!(
                    "{} {}/{} {unit}",
                    len("tooth_thickness")?,
                    signed(hi),
                    signed(lo)
                ),
                "ISO 21771-2:2025".into(),
            ],
            [
                "FLANK CLASS".into(),
                p.get("flank_tolerance_class")?.as_i64()?.to_string(),
                "ISO 1328-1:2013".into(),
            ],
        ]
        .into_iter()
        .flatten()
        .collect(),
    )
}

struct Profile {
    axis: usize,
    centre: [f64; 3],
    span: [f64; 2],
    count: u64,
}

fn profiles(a: &Analysis) -> Vec<Profile> {
    let f = frame(a);
    a.recognition
        .iter()
        .flat_map(|r| &r.evidence)
        .filter(|r| r.family == "repeating_radial_profiles")
        .filter_map(|r| {
            let p = &r.parameters;
            let axis_name = p.get("axis")?.as_str()?;
            let local_axis = ["x", "y", "z"].iter().position(|&k| axis_name == k)?;
            let direction = dir_to_part(&f, [0, 1, 2].map(|k| f64::from(k == local_axis)));
            let axis = direction.iter().position(|d| d.abs() > 1.0 - 1e-6)?;
            let centre = to_part(&f, point(p.get("centre"))?);
            let span = p.get("span")?.as_array()?;
            let mut span = [
                f[0][axis] + direction[axis] * span.first()?.as_f64()?,
                f[0][axis] + direction[axis] * span.get(1)?.as_f64()?,
            ];
            span.sort_by(f64::total_cmp);
            Some(Profile {
                axis,
                centre,
                span,
                count: p.get("repeat_count")?.as_u64()?,
            })
        })
        .collect()
}

pub fn check(a: &Analysis, drawing: &Drawing) -> Vec<LintIssue> {
    let printed = a
        .bom
        .as_ref()
        .is_some_and(|m| m.manufacturing_process().printed());
    let severity = if a.solid_count.is_some_and(|n| n > 1) {
        Severity::Info
    } else {
        Severity::Warning
    };
    let gears: Vec<_> = a
        .recognition
        .iter()
        .flat_map(|r| &r.evidence)
        .filter(|r| r.family == "authored_gears")
        .collect();
    let profiles = profiles(a);
    let mut issues = Vec::new();
    if !profiles.is_empty() && gears.is_empty() {
        // Severity orders Error < Warning < Info: cap seriousness without promoting Info.
        let severity = if printed {
            severity.max(Severity::Warning)
        } else {
            severity
        };
        issues.push(issue("gear_semantics_missing", "part", severity, "a repeating radial profile is present but no gear type, geometry, tooth thickness, or flank-tolerance requirements were declared"));
    }
    for g in gears {
        let p = &g.parameters;
        let key = p.get("id").and_then(Value::as_str).unwrap_or("gear");
        let tables: Vec<_> = drawing
            .annotations
            .iter()
            .filter(|an| {
                an.claims
                    .iter()
                    .any(|c| c.feature == key && c.parameter == "gear.requirement")
                    && drawing
                        .sheet
                        .items
                        .iter()
                        .any(|i| i.name == an.name && i.kind == Kind::Table)
            })
            .collect();
        if tables.len() != 1 {
            let code = if tables.is_empty() {
                "gear_requirement_missing"
            } else {
                "gear_requirement_ambiguous"
            };
            issues.push(issue(code, key, severity, "declared external spur gear does not have exactly one provenance-bound standards data table"));
        } else {
            let actual: Option<Vec<_>> = tables[0]
                .runs
                .iter()
                .map(|&i| Some(drawing.text.get(i)?.text.clone()))
                .collect();
            if actual.is_none() || actual != expected(p, drawing.sheet.units) {
                issues.push(issue("gear_requirement_mismatch", key, severity, "placed gear data table does not preserve the current authored normative requirement values"));
            }
        }
        let origin = point(p.get("frame").and_then(|f| f.get("origin")));
        let axis = p
            .get("frame")
            .and_then(|f| f.get("axis"))
            .and_then(Value::as_str)
            .and_then(|v| ["x", "y", "z"].iter().position(|&k| k == v));
        let width = p.get("face_width").and_then(Value::as_f64);
        let matches: Vec<_> = profiles
            .iter()
            .filter(|profile| {
                let (Some(o), Some(k), Some(w)) = (origin, axis, width) else {
                    return false;
                };
                let span = [o[k] - w / 2.0, o[k] + w / 2.0];
                (0..3).all(|k| (o[k] - profile.centre[k]).abs() <= 1e-6)
                    && (0..2).all(|k| (span[k] - profile.span[k]).abs() <= 1e-6)
            })
            .collect();
        if matches.len() != 1 {
            issues.push(issue("gear_correspondence_unverifiable", key, severity, "declared gear target has no unique full-wire repeating-profile correspondence by centre and axial span"));
            continue;
        }
        let profile = matches[0];
        if axis != Some(profile.axis) {
            issues.push(issue(
                "gear_axis_mismatch",
                key,
                severity,
                &format!(
                    "declared gear axis '{}' disagrees with independently proved profile axis '{}'",
                    axis.map_or("?", |k| ["x", "y", "z"][k]),
                    ["x", "y", "z"][profile.axis]
                ),
            ));
        }
        if p.get("tooth_count").and_then(Value::as_u64) != Some(profile.count) {
            issues.push(issue("gear_repeat_count_mismatch", key, severity, "declared gear tooth count disagrees with independently proved boundary repeat count"));
        }
    }
    issues
}
