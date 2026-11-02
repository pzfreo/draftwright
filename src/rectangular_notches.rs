//! Published rectangular through-notch geometry, shared by planning and independent audits.
//! Recognition belongs to quiddity; this module only reads its finite, file-framed record.
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
        crate::lint::parameters::direction(&frame, f.parameters.get(name))
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
    let at = crate::lint::parameters::place(&frame, f.parameters.get("at"))
        .ok_or("its record gives no bottom-wall centre")?;
    let size = |name| {
        crate::lint::parameters::size(f, name).and_then(|v| {
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

fn note_id(f: &Feature) -> String {
    format!("n:{}/location", f.id)
}

fn axes(n: &Notch) -> [usize; 2] {
    match n.axis {
        0 => [1, 2],
        1 => [0, 2],
        _ => [0, 1],
    }
}

pub(crate) fn notes(a: &Analysis, spec: &crate::model::Spec) -> Vec<crate::notes::NoteSpec> {
    if crate::dims::pmi_policy::strict(a, spec)
        || spec.dimension_source == crate::model::DimensionSource::Authored
    {
        return Vec::new();
    }
    a.features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
        .filter(|f| !spec.annotations.get(&note_id(f)).is_some_and(|s| s.omit))
        .filter_map(|f| {
            let n = read(a, f).ok()?;
            let [x, y] = axes(&n);
            Some(crate::notes::NoteSpec {
                id: note_id(f),
                text: format!(
                    "NOTCH {} MOUTH CENTRE: {} FROM LEFT; {} FROM BOTTOM IN {} VIEW.",
                    tag(f),
                    crate::dims::fmt(n.mouth[x] - a.envelope[0][x]),
                    crate::dims::fmt(n.mouth[y] - a.envelope[0][y]),
                    crate::dims::end_on(n.axis).to_uppercase()
                ),
            })
        })
        .collect()
}

pub(crate) fn note_provenance(a: &Analysis, notes: &mut [crate::notes::PlannedNote]) {
    for f in a
        .features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
    {
        if let Ok(n) = read(a, f)
            && let Some(note) = notes.iter_mut().find(|note| note.id == note_id(f))
        {
            note.default = true;
            note.basis = "tagged rectangular through-notch mouth centre, from the carrying view's left and bottom envelope extents".into();
            note.measurements = axes(&n)
                .into_iter()
                .map(|k| crate::dims::Measurement {
                    feature: f.id.clone(),
                    parameter: format!("location.length.{}", crate::lint::location::AXES[k]),
                    value: n.mouth[k] - a.envelope[0][k],
                })
                .collect();
        }
    }
}

/// A coordinate note earns location evidence only beside its live, tagged
/// feature callout, and only while its actual printed coordinates still agree.
pub(crate) fn location_evidence(
    a: &Analysis,
    drawing: &crate::output::Drawing,
    marks: &[crate::lint::Mark],
) -> Vec<crate::lint::Mark> {
    location_evidence_from_notes(a, marks, |id| {
        let annotation = drawing.annotations.iter().find(|note| note.name == id)?;
        crate::output::drawn_body(drawing, annotation)
    })
}

pub(crate) fn planned_location_evidence(
    a: &Analysis,
    plan: &crate::model::Plan,
    marks: &[crate::lint::Mark],
) -> Vec<crate::lint::Mark> {
    location_evidence_from_notes(a, marks, |id| {
        Some(
            plan.notes
                .iter()
                .find(|note| note.id == id && note.placed.is_some())?
                .rows
                .join(" "),
        )
    })
}

fn location_evidence_from_notes(
    a: &Analysis,
    marks: &[crate::lint::Mark],
    text: impl Fn(&str) -> Option<String>,
) -> Vec<crate::lint::Mark> {
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
            || !text.contains(&format!(
                "IN {} VIEW.",
                crate::dims::end_on(n.axis).to_uppercase()
            ))
        {
            continue;
        }
        for (k, edge) in axes(&n).into_iter().zip(["LEFT", "BOTTOM"]) {
            let value = n.mouth[k] - a.envelope[0][k];
            if !text
                .split_whitespace()
                .collect::<Vec<_>>()
                .windows(3)
                .any(|w| {
                    w[0].parse::<f64>().is_ok()
                        && crate::lint::holes::states(w[0], value)
                        && w[1] == "FROM"
                        && w[2].trim_end_matches(';') == edge
                })
            {
                continue;
            }
            out.push(crate::lint::Mark {
                name: format!("{}:{}", note_id(f), crate::lint::location::AXES[k]),
                view: Some(format!("v:{}", crate::dims::end_on(n.axis))),
                text: crate::dims::fmt(value),
                claims: vec![
                    crate::output::Claim {
                        feature: f.id.clone(),
                        parameter: format!("location.length.{}", crate::lint::location::AXES[k]),
                    },
                    crate::output::Claim {
                        feature: crate::lint::location::span_claim(k, a.envelope[0][k], n.mouth[k]),
                        parameter: "station.distance".into(),
                    },
                ],
            });
        }
    }
    out
}
