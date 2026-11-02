//! Rectangular through-notch geometry and evidence read independently of planning.
use crate::bridge::{Feature, holes};
use crate::model::Analysis;

pub(crate) struct Notch {
    pub axis: usize,
    pub wide: usize,
    pub deep: usize,
    pub at: [f64; 3],
    pub mouth: [f64; 3],
    pub width: f64,
    pub depth: f64,
}

pub(crate) fn read(a: &Analysis, f: &Feature) -> Result<Notch, String> {
    let frame = holes::frame(a);
    let direction = |name: &str| {
        super::parameters::direction(&frame, f.parameters.get(name))
            .ok_or_else(|| format!("its record gives no {name}"))
    };
    let run = direction("axis")?;
    let opening = direction("opening")?;
    let axis = holes::principal(run).ok_or("its thickness is not along a principal axis")?;
    let deep = holes::principal(opening).ok_or("its opening is not along a principal axis")?;
    if axis == deep {
        return Err("its opening is not perpendicular to its thickness".into());
    }
    let wide = 3 - axis - deep;
    let at = super::parameters::place(&frame, f.parameters.get("at"))
        .ok_or("its record gives no bottom-wall centre")?;
    let size = |name| {
        super::parameters::size(f, name).and_then(|v| {
            if v.is_finite() && v > 1e-6 {
                Ok(v)
            } else {
                Err(format!("its {name} is not positive"))
            }
        })
    };
    let width = size("width")?;
    let depth = size("depth")?;
    let corner_radius = f
        .parameters
        .get("corner_radius")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(0.);
    if corner_radius < 0. || corner_radius >= depth || 2. * corner_radius >= width {
        return Err("its corner radius leaves no straight bottom or side-wall witness".into());
    }
    let mouth = [0, 1, 2].map(|k| at[k] + depth * opening[k]);
    Ok(Notch {
        axis,
        wide,
        deep,
        at,
        mouth,
        width,
        depth,
    })
}

pub(crate) fn tag(f: &Feature) -> String {
    format!("N{}", f.id.rsplit(':').next().unwrap_or(&f.id))
}

pub(crate) fn note_id(f: &Feature) -> String {
    format!("n:{}/location", f.id)
}

pub(crate) fn axes(n: &Notch) -> [usize; 2] {
    match n.axis {
        0 => [1, 2],
        1 => [0, 2],
        _ => [0, 1],
    }
}

pub(crate) fn view(axis: usize) -> &'static str {
    ["side", "front", "plan"][axis]
}

/// A coordinate note earns location evidence only beside its live, tagged
/// feature callout, and only while its actual printed coordinates still agree.
pub(crate) fn location_evidence_from_notes(
    a: &Analysis,
    marks: &[super::Mark],
    text: impl Fn(&str) -> Option<String>,
) -> Vec<super::Mark> {
    let mut out = Vec::new();
    for f in a
        .features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
    {
        let Ok(n) = read(a, f) else {
            continue;
        };
        if !marks.iter().any(|m| {
            m.text.split_whitespace().any(|t| t == tag(f))
                && m.claims
                    .iter()
                    .any(|c| c.feature == f.id && c.parameter == "notch_width.length")
        }) {
            continue;
        }
        let Some(text) = text(&note_id(f)) else {
            continue;
        };
        let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if !text.contains(&format!("NOTCH {} MOUTH CENTRE:", tag(f)))
            || !text.contains(&format!("IN {} VIEW.", view(n.axis).to_uppercase()))
        {
            continue;
        }
        for (k, edge) in axes(&n).into_iter().zip(["LEFT", "BOTTOM"]) {
            let value = n.mouth[k] - a.envelope[0][k];
            let tokens = text.split_whitespace().collect::<Vec<_>>();
            let Some(printed) = tokens.windows(3).find(|w| {
                w[0].parse::<f64>().is_ok()
                    && super::holes::states(w[0], value)
                    && w[1] == "FROM"
                    && w[2].trim_end_matches(';') == edge
            }) else {
                continue;
            };
            out.push(super::Mark {
                name: format!("{}:{}", note_id(f), super::location::AXES[k]),
                view: Some(format!("v:{}", view(n.axis))),
                text: printed[0].to_owned(),
                claims: vec![
                    crate::output::Claim {
                        feature: f.id.clone(),
                        parameter: format!("location.length.{}", super::location::AXES[k]),
                    },
                    crate::output::Claim {
                        feature: super::location::span_claim(k, a.envelope[0][k], n.mouth[k]),
                        parameter: "station.distance".into(),
                    },
                ],
            });
        }
    }
    out
}
