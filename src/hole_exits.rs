//! Partly covered through-hole exits: report consulted covering geometry.
use crate::model::{Analysis, Problem};

pub(crate) fn warnings(a: &Analysis) -> Vec<Problem> {
    let mut out = Vec::new();
    for f in a.features.iter().filter(|f| f.kind == "hole") {
        for (i, o) in f
            .parameters
            .get("exit_obstructions")
            .and_then(|v| v.as_array())
            .into_iter()
            .flatten()
            .enumerate()
        {
            let covered: Vec<usize> = o["faces"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_u64().map(|n| n as usize))
                .collect();
            let features: Vec<_> = a
                .features
                .iter()
                .filter(|other| other.id != f.id && other.faces.iter().any(|i| covered.contains(i)))
                .collect();
            let names = features
                .iter()
                .map(|other| crate::wording::feature(other))
                .collect::<Vec<_>>()
                .join(", ");
            let obstruction = if names.is_empty() {
                "an adjacent face".into()
            } else {
                names
            };
            let percentage = o["covered_fraction"]
                .as_f64()
                .map_or_else(|| "unknown".into(), |f| format!("{:.0}%", 100. * f));
            out.push(Problem {
                id: format!("p:hole_exit_obstructed:{}:{i}", f.id),
                code: "hole_exit_obstructed".into(),
                severity: "warning".into(),
                message: format!(
                    "{}: exit about {percentage} covered by {obstruction}; drawn THRU — check the exit is clear.",
                    crate::wording::capital(&crate::wording::feature(f))
                ),
                subjects: std::iter::once(f.id.clone()).chain(features.iter().map(|f| f.id.clone())).collect(),
                refs: covered.iter().map(|i| format!("face:{i}")).collect(),
                ..Default::default()
            });
        }
    }
    out
}
