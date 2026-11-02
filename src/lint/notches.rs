//! Re-read notch support geometry; printed nominals, never tolerance digits, prove claims.
//!
//! quiddity's V channels (`v_channel`, #233) state their included angle and depth
//! (`v_channel.angle`, `v_channel_depth.length`), judged by [`parameters::coverage`]. One whose
//! flanks a profile V-notch includes is the notch's: the notch's own requirements above check it.
use super::parameters::{self, Wanted, fmt};
use super::{LintIssue, Mark, holes};
use crate::bridge::Feature;
use crate::model::Analysis;

/// The V channels' requirements, the included angle then the depth of each, but those of a
/// channel a profile V-notch states.
pub fn channel_wanted(a: &Analysis) -> Vec<Wanted<'_>> {
    let notches = super::notch_evidence::requirements(a);
    let mut out = Vec::new();
    for f in a.features.iter().filter(|f| {
        f.kind == "v_channel" && super::notch_evidence::channel_owner(&notches, f).is_none()
    }) {
        let angle = parameters::size(f, "included_angle_degrees");
        out.push(Wanted {
            feature: f,
            parameter: "v_channel.angle".into(),
            callout: angle
                .as_ref()
                .map_or("its included angle".into(), |v| format!("{}° V", fmt(*v))),
            values: angle.map(|v| vec![v]),
        });
        let depth = parameters::size(f, "depth");
        out.push(Wanted {
            feature: f,
            parameter: "v_channel_depth.length".into(),
            callout: depth
                .as_ref()
                .map_or("its depth".into(), |v| format!("{} DEEP", fmt(*v))),
            values: depth.map(|v| vec![v]),
        });
    }
    out
}

/// The V channel coverage findings.
pub(super) fn channel_coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let noun = |f: &Feature| {
        let mut s = format!("V channel {}", f.id);
        if let (Ok(angle), Ok(depth)) = (
            parameters::size(f, "included_angle_degrees"),
            parameters::size(f, "depth"),
        ) {
            s += &format!(" {}° × {} deep", fmt(angle), fmt(depth));
        }
        s
    };
    parameters::coverage(
        "v_channel",
        &channel_wanted(a),
        marks,
        noun,
        "the channel's callout (included angle and depth), in the view along its run",
    )
}

pub(super) fn coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut out = Vec::new();
    for n in super::notch_evidence::requirements(a) {
        let source_angles = super::notch_evidence::pmi_angles(a, &n);
        let normal_view = ["side", "front", "plan"][n.axis];
        let values = [
            ("notch.angle".to_string(), n.angle.degrees()),
            ("notch_depth.length".to_string(), n.depth()),
            (
                format!("location.length.{}", ["x", "y", "z"][n.width_axis]),
                (n.mouth[n.width_axis] - a.envelope[0][n.width_axis]).abs(),
            ),
            (
                format!("location.length.{}", ["x", "y", "z"][n.depth_axis]),
                (n.mouth[n.depth_axis] - a.envelope[0][n.depth_axis]).abs(),
            ),
        ];
        for (parameter, value) in values {
            if value < 1e-6 {
                continue;
            }
            let claimed: Vec<_> = marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == n.id && c.parameter == parameter)
                })
                .collect();
            if claimed.iter().any(|m| {
                let mut words = m.text.split_whitespace();
                let first = words.next().unwrap_or("");
                let nominal = if first.ends_with('×') {
                    words.next().unwrap_or("")
                } else {
                    first
                };
                (parameter != "notch.angle"
                    || nominal.ends_with('°')
                    || m.claims.iter().any(|c| {
                        source_angles.contains(&c.feature)
                            && matches!(c.parameter.as_str(), "angular" | "angular.basic")
                            && m.view
                                .as_deref()
                                .is_some_and(|v| v.trim_start_matches("v:") == normal_view)
                    }))
                    && holes::states(nominal, value)
            }) {
                continue;
            }
            let code = if claimed.is_empty() {
                "notch_requirement_missing"
            } else {
                "notch_requirement_unverifiable"
            };
            let mut issue = super::uncovered(
                a,
                code,
                &n.id,
                &parameter,
                format!(
                    "V-notch {} requires {value} for {parameter}, measured from its ordered supports.",
                    n.id
                ),
            );
            issue.annotation = claimed.first().map(|m| m.name.clone());
            out.push(issue);
        }
    }
    out
}

/// Rectangular U-notches use the provider's verified side walls and mouth.
/// Their locations are audited separately by the rooted physical station graph.
pub(super) fn rectangular_coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut wanted = Vec::new();
    for f in a
        .features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
    {
        for (parameter, field) in [
            ("notch_width.length", "width"),
            ("notch_depth.length", "depth"),
        ] {
            let value = super::rectangular_notches::read(a, f)
                .map(|n| if field == "width" { n.width } else { n.depth });
            wanted.push(Wanted {
                feature: f,
                parameter: parameter.into(),
                callout: value
                    .as_ref()
                    .map_or(format!("its {field}"), |v| format!("{} {field}", fmt(*v))),
                values: value.map(|v| vec![v]),
            });
        }
        for k in 0..3 {
            let geometry = super::rectangular_notches::read(a, f);
            if geometry.as_ref().is_ok_and(|n| n.axis == k) {
                continue;
            }
            let value = geometry.map(|n| n.mouth[k] - a.envelope[0][k]);
            wanted.push(Wanted {
                feature: f,
                parameter: format!("location.length.{}", ["x", "y", "z"][k]),
                callout: "its tagged mouth-centre coordinate".into(),
                values: value.map(|v| vec![v]),
            });
        }
    }
    parameters::coverage(
        "notch",
        &wanted,
        marks,
        |f| format!("rectangular through-notch {}", f.id),
        "width and depth dimensions in the view through the plate",
    )
}
