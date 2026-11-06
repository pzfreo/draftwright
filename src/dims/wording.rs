use super::{Omitted, Want};
use crate::{
    label_text::Style,
    model::{Invalid, Problem, Spec},
};

type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);

pub(super) fn apply(
    spec: &Spec,
    kept: &mut [Kept],
    given: &mut [Vec<String>],
) -> Result<(), Invalid> {
    for ((w, ..), from_spec) in kept.iter_mut().zip(given) {
        if matches!(w.shape, super::Shape::Callout { .. }) {
            w.terms = crate::callouts::wrap(&w.terms);
        }
        let Some(entry) = spec.dimensions.get(&w.id) else {
            continue;
        };
        if let Some(presentation) = entry.presentation {
            let parameter = match presentation {
                super::recesses::DepthPresentation::TangentDepth => {
                    "pocket_depth_from_tangent.length"
                }
                super::recesses::DepthPresentation::FarExtreme => {
                    "pocket_floor_from_far_extreme.length"
                }
            };
            if w.measurements.iter().any(|m| m.parameter == parameter) {
                from_spec.push("presentation".into());
                w.basis += "; applied dimensions/<id>/presentation; depth term measured from the named cylinder tangent";
            }
        }
        if entry.text.is_none() && entry.style.is_none() {
            continue;
        }
        let old = w.terms.join(" ");
        let reference = old.starts_with('(') && old.ends_with(')');
        if entry.style == Some(Style::Reference) && super::is_basic(&w.measurements) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "{} is a basic PMI dimension; remove its reference style or correct the source PMI.",
                    w.id
                ),
                choices: vec!["basic".into()],
            });
        }
        // Keep the reference enclosure outside all authored wording.
        if reference {
            w.terms = vec![old[1..old.len() - 1].into()];
        }
        if let Some(text) = &entry.text {
            w.terms = text.terms(&w.id, &w.terms)?;
            from_spec.push("text".into());
            w.basis += "; label wording from_spec: text; generated measured terms retained";
        }
        if entry.style == Some(Style::Reference) || (reference && entry.style.is_none()) {
            w.terms = vec![format!("({})", w.terms.join(" "))];
        }
        if let Some(style) = entry.style {
            let field = match style {
                Style::Reference => "style.reference",
                Style::Basic => "style.basic",
            };
            from_spec.push(field.into());
            w.basis += &format!("; from_spec: {field}");
        }
        if matches!(w.shape, super::Shape::Callout { .. }) {
            w.terms = crate::callouts::wrap(&w.terms);
        }
    }
    Ok(())
}

pub(super) fn check_covered(spec: &Spec, kept: &[Kept], omitted: &[Omitted]) -> Vec<Problem> {
    let mut problems = Vec::new();
    for (id, entry) in &spec.dimensions {
        if !entry.omit
            && (entry.text.is_some() || entry.style.is_some())
            && !kept.iter().any(|(w, ..)| &w.id == id)
            && let Some(o) = omitted.iter().find(|o| &o.id == id)
        {
            problems.push(Problem {
                id: format!("p:dimension_edit_unapplied:{id}"), code: "dimension_edit_unapplied".into(),
                severity: "warning".into(), subjects: vec![id.clone()],
                message: format!("The label edit for {} has no separate printed dimension. Its text/style is retained but could not be applied.", o.label),
                ..Default::default()
            });
        }
    }
    problems
}
