//! Factual cavity inventory, independent of which hidden edges a view shows.
use super::{LintIssue, Severity};
use crate::model::Analysis;

pub(crate) fn findings(analysis: &Analysis) -> Vec<LintIssue> {
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "interior_void")
        .map(|f| {
            let openings = f.parameters.get("openings").and_then(|v| v.as_array());
            let description = if openings.is_some_and(|o| o.is_empty()) {
                "closed internal cavity, no opening to the outside".to_string()
            } else {
                format!(
                    "internal cavity, {} recorded openings to the outside",
                    openings.map_or(0, Vec::len)
                )
            };
            let bounds = f
                .parameters
                .get("draftwright_bounds")
                .and_then(|b| serde_json::from_value::<[[f64; 3]; 2]>(b.clone()).ok());
            let size_position = bounds.map_or_else(String::new, |[lo, hi]| {
                let size = [0, 1, 2].map(|k| super::coverage::fmt(hi[k] - lo[k]));
                let at = [0, 1, 2].map(|k| super::coverage::fmt(0.5 * (hi[k] + lo[k])));
                format!(
                    "; bounding size {} × {} × {} mm, centre ({}, {}, {}) mm in the part frame",
                    size[0], size[1], size[2], at[0], at[1], at[2]
                )
            });
            LintIssue {
                id: format!("l:interior_void:{}", f.id),
                sheet: None,
                code: "interior_void".into(),
                severity: Severity::Info,
                message: format!("{}: {description}{size_position}.", f.id),
                subjects: vec![f.id.clone()],
                at: None,
                view: None,
                requirements: Vec::new(),
                measurements: Vec::new(),
                sources: Vec::new(),
                stage: None,
                annotation: None,
                related: Vec::new(),
                evidence: None,
                fix: None,
            }
        })
        .collect()
}
