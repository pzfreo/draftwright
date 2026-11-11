//! Geometry claims carry reference identities, never a trusted nominal. Re-measure them here.
use super::{LintIssue, Mark, Severity};
use crate::model::Analysis;

pub(super) fn check(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut out = Vec::new();
    for m in marks {
        for c in &m.claims {
            let Some(refs) = c.feature.strip_prefix("geometry:") else {
                continue;
            };
            let refs: Vec<_> = refs.split('|').map(String::from).collect();
            let (kind, along) = c
                .parameter
                .split_once('.')
                .map_or((c.parameter.as_str(), None), |(k, a)| (k, Some(a)));
            let measured = analysis.measure_references(&refs, kind, along);
            // Only the leading nominal may prove the claim: tolerance digits grant no credit.
            let nominal = m.text.split_whitespace().next().unwrap_or("");
            let symbol = match kind {
                "radius" => nominal.starts_with('R'),
                "diameter" => nominal.starts_with('⌀') || nominal.starts_with('ø'),
                "angle" => nominal.ends_with('°'),
                _ => true,
            };
            if symbol
                && measured
                    .as_ref()
                    .is_ok_and(|q| m.states_text(nominal, q.value))
            {
                continue;
            }
            let why = match measured {
                Ok(q) => format!(
                    "the geometry measures {} but the ink states {:?}",
                    q.value, m.text
                ),
                Err(why) => why,
            };
            out.push(LintIssue {
                sheet: None,
                id: format!("authored_dimension_invalid:{}", m.name),
                code: "authored_dimension_invalid".into(),
                severity: Severity::Error,
                message: format!("Authored dimension {} disagrees with its references: {why}.", m.name),
                at: None,
                view: m.view.clone(),
                subjects: vec![m.name.clone()],
                requirements: vec![],
                measurements: vec![],
                sources: refs.clone(),
                stage: None,
                annotation: Some(m.name.clone()),
                related: vec![],
                evidence: Some(why),
                fix: Some("Restore the measured nominal, or choose the correct geometry references and regenerate the drawing.".into()),
            });
        }
    }
    out
}
